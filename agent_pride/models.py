"""
agent_pride/models.py
=====================
Django ORM models that form the PostgreSQL persistence layer for the
Agent Pride pipeline.

Three logical tables:
  1. IngestionTelemetry  — anonymised financial fingerprint (Node 1 output).
  2. AssetOpportunity    — local Kenyan asset catalogue queried by agents (Node 3 tool).
  3. ExecutionLedger     — full audit record including CoT log (Node 5 output).
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from .schemas import (
    AssetClass,
    IncomePatternClass,
    PipelineStatus,
    RiskTier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TimestampedModel(models.Model):
    """Abstract mixin providing auto-managed created_at / updated_at fields."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Node 1 — Anonymised Ingestion Telemetry
# ---------------------------------------------------------------------------


class IngestionTelemetry(TimestampedModel):
    """
    Persists the PII-stripped financial fingerprint emitted by Node 1.

    The raw payload is *never* stored here — only the sanitised metrics.
    The `raw_schema_hash` field stores a SHA-256 of the original payload
    structure (keys only, no values) to support schema evolution audits.
    """

    session_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Correlation ID propagated through the entire pipeline.",
    )

    # --- Financial signatures ---
    balance_tier = models.PositiveSmallIntegerField(
        help_text="Discretised balance band (1–10)."
    )
    velocity_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="30-day normalised transaction velocity (0–100).",
    )
    frequency_multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        help_text="Outbound / inbound transaction count ratio.",
    )
    avg_transaction_size_tier = models.PositiveSmallIntegerField(
        help_text="Discretised average ticket size band (1–10)."
    )

    # --- Chama signals ---
    chama_participation_flag = models.BooleanField(default=False)
    chama_contribution_tier = models.PositiveSmallIntegerField(
        default=0,
        help_text="Monthly Chama contribution band (0 = non-participant).",
    )

    # --- M-Pesa signals ---
    mpesa_active_days_last_30 = models.PositiveSmallIntegerField(
        help_text="Active M-Pesa days in the last 30 calendar days."
    )
    bill_payment_regularity = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        help_text="Fraction of recurring bills paid on time (0.000–1.000).",
    )

    # --- Provenance ---
    raw_schema_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 of the inbound payload's key set (no values).",
    )
    source_channel = models.CharField(
        max_length=64,
        default="api",
        help_text="Originating channel identifier (e.g., 'api', 'batch', 'webhook').",
    )

    class Meta:
        db_table = "ap_ingestion_telemetry"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["balance_tier", "velocity_score"]),
            models.Index(fields=["chama_participation_flag"]),
        ]
        verbose_name = "Ingestion Telemetry"
        verbose_name_plural = "Ingestion Telemetry Records"

    def __str__(self) -> str:
        return f"IngestionTelemetry({self.session_id}, tier={self.balance_tier})"


# ---------------------------------------------------------------------------
# Node 3 Tool Data Source — Asset Opportunity Catalogue
# ---------------------------------------------------------------------------


class AssetOpportunity(TimestampedModel):
    """
    Local catalogue of investable asset opportunities queried by the
    Market Analyst Agent via a Django ORM tool (never an external API).

    Seeded by ops/finance teams; refreshed weekly.
    """

    opportunity_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    provider_name = models.CharField(
        max_length=120,
        help_text="Fund manager, Sacco, bank, or Treasury desk name.",
    )
    asset_class = models.CharField(
        max_length=40,
        choices=[(e.value, e.value) for e in AssetClass],
        db_index=True,
    )
    annualised_yield_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Current annualised yield (%).",
    )
    minimum_investment_kes = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Minimum investment in Kenyan Shillings.",
    )
    liquidity_days = models.PositiveSmallIntegerField(
        help_text="T+ days to access funds (0 = instant, 365 = long-lock)."
    )
    default_risk_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Historical or modelled default probability (%).",
    )
    max_leverage_ratio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=1.0,
        help_text="Maximum leverage ratio allowed on this instrument.",
    )
    suitable_risk_tiers = ArrayField(
        models.CharField(max_length=20),
        help_text="List of RiskTier values this asset suits (e.g., ['CONSERVATIVE','MODERATE']).",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Only active opportunities are surfaced to agents.",
    )
    regulatory_note = models.TextField(
        blank=True,
        default="",
        help_text="CMA / CBK / SASRA compliance note for the ops team.",
    )

    class Meta:
        db_table = "ap_asset_opportunity"
        ordering = ["-annualised_yield_pct"]
        indexes = [
            models.Index(fields=["asset_class", "is_active"]),
            models.Index(fields=["default_risk_pct"]),
        ]
        verbose_name = "Asset Opportunity"
        verbose_name_plural = "Asset Opportunities"

    def __str__(self) -> str:
        return (
            f"{self.provider_name} | {self.asset_class} "
            f"@ {self.annualised_yield_pct}% yield"
        )


# ---------------------------------------------------------------------------
# Node 5 — Execution Ledger
# ---------------------------------------------------------------------------


class ExecutionLedger(TimestampedModel):
    """
    Immutable audit record for a single completed (or failed) pipeline run.

    Stores the verified allocation outcome, all compliance check results,
    and the full Chain-of-Thought markdown generated by the CrewAI agents.
    Rows are append-only — updates are prohibited by the service layer.
    """

    ledger_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    session_id = models.UUIDField(
        db_index=True,
        help_text="Correlation ID linking this record to IngestionTelemetry.",
    )

    # --- Classification outputs ---
    income_pattern = models.CharField(
        max_length=40,
        choices=[(e.value, e.value) for e in IncomePatternClass],
    )
    risk_tier = models.CharField(
        max_length=20,
        choices=[(e.value, e.value) for e in RiskTier],
    )

    # --- Final pipeline status ---
    status = models.CharField(
        max_length=20,
        choices=[(e.value, e.value) for e in PipelineStatus],
        db_index=True,
    )
    fallback_applied = models.BooleanField(
        default=False,
        help_text="True when Node 4 overrode the Crew output with a safe fallback.",
    )

    # --- Risk metrics (Node 4 verdicts) ---
    overall_default_risk_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Blended portfolio default-risk % after Sentinel verification.",
    )
    max_leverage_ratio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Highest leverage ratio in the verified portfolio.",
    )
    sentinel_verdict = models.TextField(
        help_text="Human-readable one-line verdict from the Rank Sentinel."
    )

    # --- Recommendations & compliance audit (JSON columns) ---
    final_recommendations_json = models.JSONField(
        help_text="Serialised list of AssetRecommendationSchema dicts."
    )
    compliance_checks_json = models.JSONField(
        help_text="Serialised list of ComplianceCheckSchema dicts."
    )

    # --- Chain-of-Thought trace (TRACK Framework) ---
    chain_of_thought_md = models.TextField(
        help_text="Full Markdown CoT trace from the CrewAI run."
    )
    crew_run_duration_seconds = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        help_text="Wall-clock duration of the Node 3 CrewAI execution.",
    )

    # --- Integrity ---
    payload_checksum = models.CharField(
        max_length=64,
        help_text="SHA-256 of the serialised VerifiedAllocationSchema for tamper detection.",
    )

    class Meta:
        db_table = "ap_execution_ledger"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_id"]),
            models.Index(fields=["status", "fallback_applied"]),
            models.Index(fields=["risk_tier", "income_pattern"]),
        ]
        verbose_name = "Execution Ledger Entry"
        verbose_name_plural = "Execution Ledger Entries"

    def __str__(self) -> str:
        return (
            f"ExecutionLedger({self.ledger_id}) | "
            f"session={self.session_id} | status={self.status}"
        )