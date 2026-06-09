"""
agent_pride/services.py
=======================
Master pipeline orchestrator for the Agent Pride 5-node architecture.

AgentPridePipeline.execute_workflow() is the single public entry point.
Each node is implemented as a private method, maintaining clean separation
of concerns and allowing each stage to be unit-tested independently.

Node execution order:
  1. _node1_ingest()        -- PII sanitisation + DB persist
  2. _node2_classify()      -- Telemetry -> ExecutionIntent
  3. _node3_crew_execute()  -- CrewAI orchestration
  4. _node4_sentinel()      -- Deterministic compliance verification
  5. _node5_persist()       -- Ledger write + post-save signal
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from decimal import Decimal
from typing import Any

from crewai import Crew, Process
from django.db import transaction
from django.dispatch import Signal
from pydantic import ValidationError

from .agents import build_allocation_architect_agent, build_market_analyst_agent
from .models import ExecutionLedger, IngestionTelemetry
from .schemas import (
    AnonymisedTelemetrySchema,
    AssetClass,
    AssetRecommendationSchema,
    ComplianceCheckSchema,
    CrewOutputSchema,
    ExecutionIntentSchema,
    IncomePatternClass,
    LedgerEntrySchema,
    PipelineStatus,
    RiskTier,
    VerifiedAllocationSchema,
)
from .tasks import build_allocation_strategy_task, build_market_research_task

logger = logging.getLogger(__name__)

# Django signal emitted after a successful pipeline run (Node 5).
pipeline_completed = Signal()

# ---------------------------------------------------------------------------
# Compliance rule constants (Node 4 guardrails)
# ---------------------------------------------------------------------------

MAX_DEFAULT_RISK_PCT: float = 3.0
MAX_LEVERAGE_RATIO: float = 2.0
MAX_SINGLE_ALLOCATION_PCT: float = 60.0
MIN_YIELD_PCT: float = 0.5
MAX_YIELD_PCT: float = 25.0

FALLBACK_RECOMMENDATION = AssetRecommendationSchema(
    asset_class=AssetClass.MMF,
    allocation_pct=100.0,
    expected_yield_pct=10.5,
    provider_name="CIC Money Market Fund (Safe Fallback)",
    rationale=(
        "Sentinel guardrail triggered: original recommendations violated compliance "
        "bounds. Portfolio collapsed to a 100% liquid Money Market Fund as the "
        "approved safe-harbour asset."
    ),
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Raised when an unrecoverable error occurs within the pipeline."""


class SanitisationError(PipelineError):
    """Raised when the raw payload cannot be safely sanitised."""


class ClassificationError(PipelineError):
    """Raised when Node 2 cannot produce a valid ExecutionIntent."""


class CrewExecutionError(PipelineError):
    """Raised when the CrewAI orchestration loop fails or returns invalid output."""


class PersistenceError(PipelineError):
    """Raised when the Node 5 database write fails."""


# ---------------------------------------------------------------------------
# Master Orchestrator
# ---------------------------------------------------------------------------


class AgentPridePipeline:
    """
    Master orchestrator for the 5-node Agent Pride pipeline.

    Usage::

        pipeline = AgentPridePipeline()
        ledger_id = pipeline.execute_workflow(raw_payload)

    The ``execute_workflow`` method is thread-safe; each call is fully
    self-contained with its own session_id and database transaction.
    """

    _PII_FIELDS: frozenset[str] = frozenset(
        {
            "phone_number", "msisdn", "full_name", "first_name", "last_name",
            "national_id", "id_number", "email", "email_address",
            "account_number", "bank_account", "ip_address", "device_id",
            "latitude", "longitude", "home_address", "postal_address",
            "date_of_birth", "dob", "kra_pin", "passport_number",
        }
    )

    def execute_workflow(self, raw_payload: dict[str, Any]) -> uuid.UUID:
        """
        Execute the complete 5-node Agent Pride pipeline.

        Args:
            raw_payload: Unprocessed inbound dictionary from the DRF view.
                         May contain PII -- this method handles sanitisation.

        Returns:
            UUID of the created ExecutionLedger record (ledger_id).

        Raises:
            PipelineError: If any node encounters an unrecoverable error.
        """
        session_id = uuid.uuid4()
        logger.info("Pipeline started. session_id=%s", session_id)

        try:
            telemetry = self._node1_ingest(raw_payload, session_id)
            logger.info("[Node 1] Ingestion complete. session_id=%s", session_id)

            intent = self._node2_classify(telemetry)
            logger.info(
                "[Node 2] Classification complete. pattern=%s tier=%s session_id=%s",
                intent.income_pattern.value, intent.risk_tier.value, session_id,
            )

            crew_output = self._node3_crew_execute(intent)
            logger.info(
                "[Node 3] Crew execution complete. recommendations=%d session_id=%s",
                len(crew_output.recommendations), session_id,
            )

            verified = self._node4_sentinel(crew_output)
            logger.info(
                "[Node 4] Sentinel verdict: %s | fallback=%s session_id=%s",
                verified.sentinel_verdict, verified.fallback_applied, session_id,
            )

            ledger_id = self._node5_persist(intent, crew_output, verified)
            logger.info(
                "[Node 5] Ledger persisted. ledger_id=%s session_id=%s",
                ledger_id, session_id,
            )

            return ledger_id

        except PipelineError:
            raise
        except Exception as exc:
            logger.exception("Unexpected pipeline failure. session_id=%s", session_id)
            raise PipelineError(
                f"Unexpected pipeline failure at session {session_id}: {exc}"
            ) from exc

    # -----------------------------------------------------------------------
    # Node 1 -- The Watering Hole (Ingestion Hub)
    # -----------------------------------------------------------------------

    def _node1_ingest(
        self,
        raw_payload: dict[str, Any],
        session_id: uuid.UUID,
    ) -> AnonymisedTelemetrySchema:
        """
        Strip PII from the raw payload, persist an anonymised record to
        IngestionTelemetry, and return a validated AnonymisedTelemetrySchema.

        Steps:
          1. Compute a SHA-256 schema hash from payload keys only (no values).
          2. Remove all recognised PII fields from a copy of the payload.
          3. Validate the sanitised fields against AnonymisedTelemetrySchema.
          4. Persist the anonymised record inside an atomic transaction.

        Args:
            raw_payload: Unprocessed inbound dict (may contain PII).
            session_id: Pipeline correlation UUID.

        Returns:
            Validated AnonymisedTelemetrySchema with no PII fields.

        Raises:
            SanitisationError: On Pydantic validation failure or DB write error.
        """
        try:
            schema_hash = hashlib.sha256(
                json.dumps(sorted(raw_payload.keys()), sort_keys=True).encode()
            ).hexdigest()

            sanitised = {
                k: v for k, v in raw_payload.items()
                if k.lower() not in self._PII_FIELDS
            }
            sanitised["session_id"] = str(session_id)

            try:
                telemetry = AnonymisedTelemetrySchema(**sanitised)
            except ValidationError as ve:
                raise SanitisationError(
                    f"Payload failed AnonymisedTelemetrySchema validation: {ve}"
                ) from ve

            source_channel = sanitised.get("source_channel", "api")

            with transaction.atomic():
                IngestionTelemetry.objects.create(
                    session_id=telemetry.session_id,
                    balance_tier=telemetry.balance_tier,
                    velocity_score=Decimal(str(telemetry.velocity_score)),
                    frequency_multiplier=Decimal(str(telemetry.frequency_multiplier)),
                    avg_transaction_size_tier=telemetry.avg_transaction_size_tier,
                    chama_participation_flag=telemetry.chama_participation_flag,
                    chama_contribution_tier=telemetry.chama_contribution_tier,
                    mpesa_active_days_last_30=telemetry.mpesa_active_days_last_30,
                    bill_payment_regularity=Decimal(str(telemetry.bill_payment_regularity)),
                    raw_schema_hash=schema_hash,
                    source_channel=str(source_channel),
                )

            return telemetry

        except SanitisationError:
            raise
        except Exception as exc:
            logger.exception("[Node 1] Ingestion failure.")
            raise SanitisationError(f"Node 1 ingestion failed: {exc}") from exc

    # -----------------------------------------------------------------------
    # Node 2 -- The Trail Finder (Tracking Engine)
    # -----------------------------------------------------------------------

    def _node2_classify(
        self,
        telemetry: AnonymisedTelemetrySchema,
    ) -> ExecutionIntentSchema:
        """
        Transform anonymised telemetry into a structured ExecutionIntentSchema
        using deterministic scoring heuristics.

        No ML model or LLM is invoked -- this is a reproducible rule-based
        classifier ensuring full auditability.

        Args:
            telemetry: Validated AnonymisedTelemetrySchema from Node 1.

        Returns:
            ExecutionIntentSchema ready for the CrewAI agents in Node 3.

        Raises:
            ClassificationError: If intent construction fails Pydantic validation.
        """
        try:
            income_pattern = self._classify_income_pattern(telemetry)
            risk_tier = self._classify_risk_tier(telemetry)
            investable_capacity = self._compute_investable_capacity(telemetry)
            preferred_assets = self._resolve_preferred_assets(income_pattern, risk_tier)
            liquidity_pref = self._compute_liquidity_preference(telemetry, income_pattern)
            horizon_months = self._estimate_horizon(telemetry, income_pattern)

            return ExecutionIntentSchema(
                session_id=telemetry.session_id,
                income_pattern=income_pattern,
                risk_tier=risk_tier,
                investable_capacity_score=investable_capacity,
                preferred_asset_classes=preferred_assets,
                liquidity_preference=liquidity_pref,
                horizon_months=horizon_months,
            )

        except ValidationError as ve:
            raise ClassificationError(
                f"ExecutionIntentSchema validation failed: {ve}"
            ) from ve
        except Exception as exc:
            logger.exception("[Node 2] Classification failure.")
            raise ClassificationError(f"Node 2 classification failed: {exc}") from exc

    @staticmethod
    def _classify_income_pattern(t: AnonymisedTelemetrySchema) -> IncomePatternClass:
        """Apply deterministic rule tree to classify the income pattern."""
        if t.chama_participation_flag and t.chama_contribution_tier >= 5:
            return IncomePatternClass.CHAMA_POOLED
        if t.velocity_score >= 70 and t.balance_tier <= 5:
            return IncomePatternClass.HIGH_VELOCITY_LIQUID
        if t.mpesa_active_days_last_30 < 10:
            return IncomePatternClass.IRREGULAR_GIG
        if t.velocity_score <= 30 and t.balance_tier >= 7:
            return IncomePatternClass.LOW_VELOCITY_ACCUMULATION
        return IncomePatternClass.MEDIUM_VELOCITY_MIXED

    @staticmethod
    def _classify_risk_tier(t: AnonymisedTelemetrySchema) -> RiskTier:
        """
        Map financial signatures to an underwriting risk tier using a
        simple additive scoring model.

        Score map:
          bill_payment_regularity >= 0.9  +2
          balance_tier >= 7               +2
          velocity_score >= 60            +1
          mpesa_active_days >= 20         +1
          frequency_multiplier > 5        -1 (high outbound spend ratio)
          chama_contribution_tier >= 3    +1

          0-2  -> CONSERVATIVE
          3-4  -> MODERATE
          5-6  -> GROWTH
          7+   -> AGGRESSIVE
        """
        score = 0
        if t.bill_payment_regularity >= 0.9:
            score += 2
        if t.balance_tier >= 7:
            score += 2
        if t.velocity_score >= 60:
            score += 1
        if t.mpesa_active_days_last_30 >= 20:
            score += 1
        if t.frequency_multiplier > 5:
            score -= 1
        if t.chama_contribution_tier >= 3:
            score += 1

        if score <= 2:
            return RiskTier.CONSERVATIVE
        if score <= 4:
            return RiskTier.MODERATE
        if score <= 6:
            return RiskTier.GROWTH
        return RiskTier.AGGRESSIVE

    @staticmethod
    def _compute_investable_capacity(t: AnonymisedTelemetrySchema) -> float:
        """
        Produce a normalised investable-capacity score (0-100) composed of:
          - balance_tier        weighted 40%
          - bill_payment_regularity weighted 35%
          - mpesa_active_days   weighted 25%
        """
        raw = (
            (t.balance_tier / 10) * 40
            + t.bill_payment_regularity * 35
            + (t.mpesa_active_days_last_30 / 30) * 25
        )
        return round(min(max(raw, 0.0), 100.0), 2)

    @staticmethod
    def _resolve_preferred_assets(
        pattern: IncomePatternClass,
        tier: RiskTier,
    ) -> list[AssetClass]:
        """
        Return an ordered list of preferred asset classes derived from
        the (income_pattern, risk_tier) combination.
        """
        mapping: dict[tuple[IncomePatternClass, RiskTier], list[AssetClass]] = {
            (IncomePatternClass.HIGH_VELOCITY_LIQUID, RiskTier.CONSERVATIVE): [
                AssetClass.MMF, AssetClass.SACCO_DEPOSIT,
            ],
            (IncomePatternClass.HIGH_VELOCITY_LIQUID, RiskTier.MODERATE): [
                AssetClass.MMF, AssetClass.TREASURY_BILL, AssetClass.SACCO_DEPOSIT,
            ],
            (IncomePatternClass.HIGH_VELOCITY_LIQUID, RiskTier.GROWTH): [
                AssetClass.MMF, AssetClass.TREASURY_BILL, AssetClass.UNIT_TRUST_EQUITY,
            ],
            (IncomePatternClass.HIGH_VELOCITY_LIQUID, RiskTier.AGGRESSIVE): [
                AssetClass.UNIT_TRUST_EQUITY, AssetClass.TREASURY_BILL, AssetClass.MMF,
            ],
            (IncomePatternClass.LOW_VELOCITY_ACCUMULATION, RiskTier.CONSERVATIVE): [
                AssetClass.SACCO_DEPOSIT, AssetClass.GOVERNMENT_BOND,
            ],
            (IncomePatternClass.LOW_VELOCITY_ACCUMULATION, RiskTier.MODERATE): [
                AssetClass.GOVERNMENT_BOND, AssetClass.SACCO_DEPOSIT, AssetClass.MMF,
            ],
            (IncomePatternClass.LOW_VELOCITY_ACCUMULATION, RiskTier.GROWTH): [
                AssetClass.GOVERNMENT_BOND, AssetClass.UNIT_TRUST_EQUITY, AssetClass.SACCO_DEPOSIT,
            ],
            (IncomePatternClass.LOW_VELOCITY_ACCUMULATION, RiskTier.AGGRESSIVE): [
                AssetClass.UNIT_TRUST_EQUITY, AssetClass.GOVERNMENT_BOND, AssetClass.TREASURY_BILL,
            ],
            (IncomePatternClass.CHAMA_POOLED, RiskTier.CONSERVATIVE): [
                AssetClass.CHAMA_CONTRIBUTION, AssetClass.SACCO_DEPOSIT, AssetClass.MMF,
            ],
            (IncomePatternClass.CHAMA_POOLED, RiskTier.MODERATE): [
                AssetClass.CHAMA_CONTRIBUTION, AssetClass.SACCO_DEPOSIT, AssetClass.GOVERNMENT_BOND,
            ],
            (IncomePatternClass.CHAMA_POOLED, RiskTier.GROWTH): [
                AssetClass.CHAMA_CONTRIBUTION, AssetClass.UNIT_TRUST_EQUITY, AssetClass.SACCO_DEPOSIT,
            ],
            (IncomePatternClass.CHAMA_POOLED, RiskTier.AGGRESSIVE): [
                AssetClass.CHAMA_CONTRIBUTION, AssetClass.UNIT_TRUST_EQUITY, AssetClass.GOVERNMENT_BOND,
            ],
            (IncomePatternClass.IRREGULAR_GIG, RiskTier.CONSERVATIVE): [AssetClass.MMF],
            (IncomePatternClass.IRREGULAR_GIG, RiskTier.MODERATE): [
                AssetClass.MMF, AssetClass.TREASURY_BILL,
            ],
            (IncomePatternClass.IRREGULAR_GIG, RiskTier.GROWTH): [
                AssetClass.MMF, AssetClass.TREASURY_BILL, AssetClass.UNIT_TRUST_EQUITY,
            ],
            (IncomePatternClass.IRREGULAR_GIG, RiskTier.AGGRESSIVE): [
                AssetClass.UNIT_TRUST_EQUITY, AssetClass.MMF, AssetClass.TREASURY_BILL,
            ],
        }
        return mapping.get((pattern, tier), [AssetClass.MMF, AssetClass.SACCO_DEPOSIT])

    @staticmethod
    def _compute_liquidity_preference(
        t: AnonymisedTelemetrySchema,
        pattern: IncomePatternClass,
    ) -> float:
        """
        Estimate the required liquidity fraction (0.0-1.0) based on income
        pattern and bill-payment stress signals.
        """
        base: dict[IncomePatternClass, float] = {
            IncomePatternClass.HIGH_VELOCITY_LIQUID: 0.6,
            IncomePatternClass.MEDIUM_VELOCITY_MIXED: 0.5,
            IncomePatternClass.LOW_VELOCITY_ACCUMULATION: 0.3,
            IncomePatternClass.IRREGULAR_GIG: 0.8,
            IncomePatternClass.CHAMA_POOLED: 0.45,
        }
        pref = base.get(pattern, 0.5)
        if t.bill_payment_regularity < 0.5:
            pref = min(pref + 0.15, 1.0)
        return round(pref, 3)

    @staticmethod
    def _estimate_horizon(
        t: AnonymisedTelemetrySchema,
        pattern: IncomePatternClass,
    ) -> int:
        """
        Estimate investment horizon in months from income pattern archetype.
        Reduced by 6 months when balance_tier is low (limited financial cushion).
        """
        base: dict[IncomePatternClass, int] = {
            IncomePatternClass.CHAMA_POOLED: 24,
            IncomePatternClass.LOW_VELOCITY_ACCUMULATION: 36,
            IncomePatternClass.MEDIUM_VELOCITY_MIXED: 18,
            IncomePatternClass.HIGH_VELOCITY_LIQUID: 12,
            IncomePatternClass.IRREGULAR_GIG: 6,
        }
        horizon = base.get(pattern, 12)
        if t.balance_tier <= 3:
            horizon = max(horizon - 6, 3)
        return horizon

    # -----------------------------------------------------------------------
    # Node 3 -- The Hunt Catalyst (CrewAI Processing Core)
    # -----------------------------------------------------------------------

    def _node3_crew_execute(
        self,
        intent: ExecutionIntentSchema,
    ) -> CrewOutputSchema:
        """
        Instantiate and run the CrewAI orchestration loop with the Market
        Analyst and Allocation Architect agents.

        Agents receive ONLY non-PII execution intent fields embedded in
        task descriptions -- no raw payload reaches them at any point.

        Args:
            intent: Validated ExecutionIntentSchema from Node 2.

        Returns:
            Validated CrewOutputSchema parsed from the Crew's final string output.

        Raises:
            CrewExecutionError: If the Crew run fails or produces invalid output.
        """
        analyst_agent = build_market_analyst_agent()
        architect_agent = build_allocation_architect_agent()

        market_task = build_market_research_task(analyst_agent, intent)
        allocation_task = build_allocation_strategy_task(
            architect_agent, intent, context_tasks=[market_task]
        )

        crew = Crew(
            agents=[analyst_agent, architect_agent],
            tasks=[market_task, allocation_task],
            process=Process.sequential,
            verbose=True,
        )

        start_time = time.monotonic()
        try:
            crew_result = crew.kickoff()
        except Exception as exc:
            logger.exception("[Node 3] CrewAI kickoff failed.")
            raise CrewExecutionError(
                f"CrewAI orchestration loop failed: {exc}"
            ) from exc
        duration = time.monotonic() - start_time

        raw_output: str = (
            crew_result.raw if hasattr(crew_result, "raw") else str(crew_result)
        )
        return self._parse_crew_output(raw_output, intent.session_id, duration)

    def _parse_crew_output(
        self,
        raw_output: str,
        session_id: uuid.UUID,
        duration: float,
    ) -> CrewOutputSchema:
        """
        Parse and validate the raw string output from CrewAI into a typed
        CrewOutputSchema, handling common LLM output artefacts.

        Strategies applied (in order):
          1. Strip Markdown code fences.
          2. Locate the outermost JSON object via regex.
          3. Parse with json.loads.
          4. Inject pipeline metadata (session_id, duration).
          5. Validate with Pydantic.

        Args:
            raw_output: Raw string from crew.kickoff().
            session_id: Pipeline session UUID.
            duration: Wall-clock Crew run duration in seconds.

        Returns:
            Validated CrewOutputSchema.

        Raises:
            CrewExecutionError: If extraction or Pydantic validation fails.
        """
        cleaned = re.sub(r"```(?:json)?\s*", "", raw_output).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not json_match:
            raise CrewExecutionError(
                "No JSON object found in Crew output. "
                f"Raw output (first 500 chars): {raw_output[:500]}"
            )

        try:
            parsed_dict = json.loads(json_match.group(0))
        except json.JSONDecodeError as exc:
            raise CrewExecutionError(
                f"Crew output JSON is malformed: {exc}. "
                f"Extracted string (first 500 chars): {json_match.group(0)[:500]}"
            ) from exc

        parsed_dict["session_id"] = str(session_id)
        parsed_dict["crew_run_duration_seconds"] = round(duration, 3)

        if "chain_of_thought_md" not in parsed_dict:
            parsed_dict["chain_of_thought_md"] = (
                "_Chain-of-thought trace not produced by the Allocation Architect. "
                "Raw crew output preserved in the ledger._"
            )

        try:
            return CrewOutputSchema(**parsed_dict)
        except ValidationError as ve:
            raise CrewExecutionError(
                f"Crew output failed CrewOutputSchema validation: {ve}"
            ) from ve

    # -----------------------------------------------------------------------
    # Node 4 -- The Rank Sentinel (Alignment Ledger)
    # -----------------------------------------------------------------------

    def _node4_sentinel(
        self,
        crew_output: CrewOutputSchema,
    ) -> VerifiedAllocationSchema:
        """
        Deterministic, non-LLM compliance verification engine.

        Guardrails evaluated:
          RULE_DR_001  Blended portfolio default risk < 3%
          RULE_LV_002  Max leverage ratio <= 2.0
          RULE_SA_003  No single asset class allocation > 60%
          RULE_YC_004  No yield claim > 25% (hallucination cap)
          RULE_YF_005  No yield claim < 0.5% (suspect zero-return)

        If ANY rule is violated, the full recommendation set is replaced
        with FALLBACK_RECOMMENDATION and fallback_applied is set to True.

        Args:
            crew_output: Validated CrewOutputSchema from Node 3.

        Returns:
            VerifiedAllocationSchema with a complete compliance audit trail.
        """
        checks: list[ComplianceCheckSchema] = []
        violations_found = False
        recs = list(crew_output.recommendations)

        # Defensive: if no recommendations are provided, treat as a violation
        # and collapse to safe-harbour fallback.
        if not recs:
            checks.append(
                ComplianceCheckSchema(
                    rule_id="RULE_EMPTY_006",
                    description="Crew output must include at least one recommendation.",
                    passed=False,
                    observed_value=0.0,
                    threshold=1.0,
                )
            )
            violations_found = True
            final_recs = [FALLBACK_RECOMMENDATION]
            fallback_applied = True
            violated_rules = [c.rule_id for c in checks if not c.passed]
            sentinel_verdict = (
                f"SENTINEL OVERRIDE: No recommendations returned. "
                f"Collapsed to safe-harbour MMF fallback. Violated {violated_rules}."
            )
            blended_default_risk = self._estimate_blended_default_risk(final_recs)
            max_leverage = self._resolve_max_leverage(final_recs)
            return VerifiedAllocationSchema(
                session_id=crew_output.session_id,
                final_recommendations=final_recs,
                compliance_checks=checks,
                fallback_applied=fallback_applied,
                overall_default_risk_pct=round(blended_default_risk, 3),
                max_leverage_ratio=round(max_leverage, 3),
                sentinel_verdict=sentinel_verdict,
            )

        # RULE_DR_001: Blended default risk
        blended_default_risk = self._estimate_blended_default_risk(recs)

        dr_pass = blended_default_risk < MAX_DEFAULT_RISK_PCT
        checks.append(ComplianceCheckSchema(
            rule_id="RULE_DR_001",
            description=f"Blended portfolio default risk must be < {MAX_DEFAULT_RISK_PCT}%.",
            passed=dr_pass,
            observed_value=round(blended_default_risk, 3),
            threshold=MAX_DEFAULT_RISK_PCT,
        ))
        if not dr_pass:
            violations_found = True
            logger.warning("[Node 4] RULE_DR_001 violated: blended_default_risk=%.3f%%", blended_default_risk)

        # RULE_LV_002: Leverage ratio
        max_leverage = self._resolve_max_leverage(recs)
        lv_pass = max_leverage <= MAX_LEVERAGE_RATIO
        checks.append(ComplianceCheckSchema(
            rule_id="RULE_LV_002",
            description=f"Maximum leverage ratio must be <= {MAX_LEVERAGE_RATIO}.",
            passed=lv_pass,
            observed_value=round(max_leverage, 3),
            threshold=MAX_LEVERAGE_RATIO,
        ))
        if not lv_pass:
            violations_found = True
            logger.warning("[Node 4] RULE_LV_002 violated: max_leverage=%.3f", max_leverage)

        # RULE_SA_003: Single-asset concentration
        max_single_alloc = max(r.allocation_pct for r in recs)
        sa_pass = max_single_alloc <= MAX_SINGLE_ALLOCATION_PCT
        checks.append(ComplianceCheckSchema(
            rule_id="RULE_SA_003",
            description=f"No single allocation may exceed {MAX_SINGLE_ALLOCATION_PCT}% of the portfolio.",
            passed=sa_pass,
            observed_value=round(max_single_alloc, 2),
            threshold=MAX_SINGLE_ALLOCATION_PCT,
        ))
        if not sa_pass:
            violations_found = True
            logger.warning("[Node 4] RULE_SA_003 violated: max_single_alloc=%.2f%%", max_single_alloc)

        # RULE_YC_004: Yield hallucination ceiling
        max_yield = max(r.expected_yield_pct for r in recs)
        yc_pass = max_yield <= MAX_YIELD_PCT
        checks.append(ComplianceCheckSchema(
            rule_id="RULE_YC_004",
            description=f"No yield claim may exceed {MAX_YIELD_PCT}% (hallucination cap).",
            passed=yc_pass,
            observed_value=round(max_yield, 2),
            threshold=MAX_YIELD_PCT,
        ))
        if not yc_pass:
            violations_found = True
            logger.warning("[Node 4] RULE_YC_004 violated: max_yield=%.2f%%", max_yield)

        # RULE_YF_005: Minimum yield floor
        min_yield = min(r.expected_yield_pct for r in recs)
        yf_pass = min_yield >= MIN_YIELD_PCT
        checks.append(ComplianceCheckSchema(
            rule_id="RULE_YF_005",
            description=f"No allocation yield may be < {MIN_YIELD_PCT}% (suspect zero-return).",
            passed=yf_pass,
            observed_value=round(min_yield, 2),
            threshold=MIN_YIELD_PCT,
        ))
        if not yf_pass:
            violations_found = True
            logger.warning("[Node 4] RULE_YF_005 violated: min_yield=%.2f%%", min_yield)

        if violations_found:
            final_recs = [FALLBACK_RECOMMENDATION]
            fallback_applied = True
            violated_rules = [c.rule_id for c in checks if not c.passed]
            sentinel_verdict = (
                f"SENTINEL OVERRIDE: Compliance guardrails violated {violated_rules}. "
                "Portfolio collapsed to safe-harbour MMF fallback."
            )
            blended_default_risk = self._estimate_blended_default_risk(final_recs)
            max_leverage = self._resolve_max_leverage(final_recs)
        else:
            final_recs = recs
            fallback_applied = False
            sentinel_verdict = (
                f"SENTINEL PASS: All {len(checks)} compliance rules satisfied. "
                "Original Crew recommendations approved."
            )

        return VerifiedAllocationSchema(
            session_id=crew_output.session_id,
            final_recommendations=final_recs,
            compliance_checks=checks,
            fallback_applied=fallback_applied,
            overall_default_risk_pct=round(blended_default_risk, 3),
            max_leverage_ratio=round(max_leverage, 3),
            sentinel_verdict=sentinel_verdict,
        )

    @staticmethod
    def _estimate_blended_default_risk(
        recs: list[AssetRecommendationSchema],
    ) -> float:
        """
        Compute a weighted-average blended default risk by joining each
        recommendation against the AssetOpportunity catalogue.

        Falls back to a yield-proxy heuristic (yield * 0.5) when no
        matching catalogue record is found for a given provider.

        Args:
            recs: List of asset recommendations to evaluate.

        Returns:
            Blended default risk percentage (0.0-100.0).
        """
        from .models import AssetOpportunity  # noqa: PLC0415

        total_weight = 0.0
        weighted_risk = 0.0

        for rec in recs:
            weight = rec.allocation_pct / 100.0
            total_weight += weight
            try:
                opp = AssetOpportunity.objects.filter(
                    provider_name__icontains=rec.provider_name.split("(")[0].strip(),
                    is_active=True,
                ).first()
                if opp:
                    default_risk = float(opp.default_risk_pct)
                else:
                    from django.db.models import Avg  # noqa: PLC0415
                    avg = AssetOpportunity.objects.filter(
                        asset_class=rec.asset_class.value, is_active=True,
                    ).aggregate(avg_risk=Avg("default_risk_pct"))
                    default_risk = float(avg["avg_risk"] or rec.expected_yield_pct * 0.5)
            except Exception:
                default_risk = rec.expected_yield_pct * 0.5
            weighted_risk += weight * default_risk

        return weighted_risk / total_weight if total_weight > 0 else 0.0

    @staticmethod
    def _resolve_max_leverage(
        recs: list[AssetRecommendationSchema],
    ) -> float:
        """
        Determine the maximum leverage ratio across all recommended instruments
        via an AssetOpportunity catalogue lookup. Defaults to 1.0 (no leverage).

        Args:
            recs: List of asset recommendations.

        Returns:
            Maximum observed leverage ratio.
        """
        from .models import AssetOpportunity  # noqa: PLC0415

        max_lv = 1.0
        for rec in recs:
            try:
                opp = AssetOpportunity.objects.filter(
                    provider_name__icontains=rec.provider_name.split("(")[0].strip(),
                    is_active=True,
                ).first()
                if opp:
                    max_lv = max(max_lv, float(opp.max_leverage_ratio))
            except Exception:
                pass
        return max_lv

    # -----------------------------------------------------------------------
    # Node 5 -- The Guardian Agent (Systemic Protector)
    # -----------------------------------------------------------------------

    def _node5_persist(
        self,
        intent: ExecutionIntentSchema,
        crew_output: CrewOutputSchema,
        verified: VerifiedAllocationSchema,
    ) -> uuid.UUID:
        """
        Persist the verified allocation and Chain-of-Thought audit trail to
        the Django PostgreSQL ExecutionLedger, then emit the pipeline_completed
        signal to simulate a downstream WebSocket notification.

        The ledger row is written inside an atomic transaction. The signal is
        fired only after the transaction commits, ensuring the row is visible
        to any signal handlers that query the database.

        Args:
            intent: ExecutionIntentSchema from Node 2.
            crew_output: CrewOutputSchema from Node 3.
            verified: VerifiedAllocationSchema from Node 4.

        Returns:
            UUID of the created ExecutionLedger record.

        Raises:
            PersistenceError: If the database write fails.
        """
        ledger_id = uuid.uuid4()

        ledger_schema = LedgerEntrySchema(
            ledger_id=ledger_id,
            session_id=verified.session_id,
            status=(
                PipelineStatus.FALLBACK_APPLIED
                if verified.fallback_applied
                else PipelineStatus.SUCCESS
            ),
            income_pattern=intent.income_pattern,
            risk_tier=intent.risk_tier,
            fallback_applied=verified.fallback_applied,
            overall_default_risk_pct=Decimal(str(round(verified.overall_default_risk_pct, 2))),
            final_recommendations=list(verified.final_recommendations),
            compliance_checks=list(verified.compliance_checks),
            chain_of_thought_md=crew_output.chain_of_thought_md,
            sentinel_verdict=verified.sentinel_verdict,
            crew_run_duration_seconds=Decimal(
                str(round(float(crew_output.crew_run_duration_seconds), 3))
            ),
        )

        payload_checksum = hashlib.sha256(
            ledger_schema.model_dump_json().encode("utf-8")
        ).hexdigest()

        try:
            with transaction.atomic():
                ledger = ExecutionLedger.objects.create(
                    ledger_id=ledger_id,
                    session_id=verified.session_id,
                    income_pattern=intent.income_pattern.value,
                    risk_tier=intent.risk_tier.value,
                    status=ledger_schema.status.value,
                    fallback_applied=verified.fallback_applied,
                    overall_default_risk_pct=Decimal(
                        str(round(verified.overall_default_risk_pct, 2))
                    ),
                    max_leverage_ratio=Decimal(str(round(verified.max_leverage_ratio, 2))),
                    sentinel_verdict=verified.sentinel_verdict,
                    final_recommendations_json=[
                        r.model_dump() for r in verified.final_recommendations
                    ],
                    compliance_checks_json=[
                        c.model_dump() for c in verified.compliance_checks
                    ],
                    chain_of_thought_md=crew_output.chain_of_thought_md,
                    crew_run_duration_seconds=Decimal(
                        str(round(float(crew_output.crew_run_duration_seconds), 3))
                    ),
                    payload_checksum=payload_checksum,
                )

        except Exception as exc:
            logger.exception("[Node 5] Ledger persistence failed.")
            raise PersistenceError(
                f"Node 5 database write failed for session {verified.session_id}: {exc}"
            ) from exc

        self._emit_completion_signal(ledger, ledger_schema)
        return ledger_id


    @staticmethod
    def _emit_completion_signal(
        ledger: ExecutionLedger,
        ledger_schema: LedgerEntrySchema,
    ) -> None:
        """
        Fire the ``pipeline_completed`` Django signal after a successful ledger write.

        In production this connects to a Django Channels WebSocket layer handler
        that pushes the result JSON to a subscribed frontend client.

        Signal dispatch failures are swallowed with a WARNING log -- they must
        never abort or roll back the pipeline response.

        Args:
            ledger: Newly created ExecutionLedger ORM instance.
            ledger_schema: Validated LedgerEntrySchema for rich payload access.
        """
        signal_payload: dict[str, Any] = {
            "ledger_id": str(ledger.ledger_id),
            "session_id": str(ledger.session_id),
            "status": ledger.status,
            "income_pattern": ledger.income_pattern,
            "risk_tier": ledger.risk_tier,
            "fallback_applied": ledger.fallback_applied,
            "overall_default_risk_pct": float(ledger.overall_default_risk_pct),
            "sentinel_verdict": ledger.sentinel_verdict,
            "recommendation_count": len(ledger_schema.final_recommendations),
            "created_at": ledger.created_at.isoformat(),
        }
        try:
            pipeline_completed.send(
                sender=AgentPridePipeline,
                payload=signal_payload,
            )
            logger.info(
                "[Node 5] pipeline_completed signal fired. ledger_id=%s",
                ledger.ledger_id,
            )
        except Exception as exc:
            logger.warning(
                "[Node 5] pipeline_completed signal dispatch failed: %s. "
                "Ledger record was still persisted successfully.",
                exc,
            )