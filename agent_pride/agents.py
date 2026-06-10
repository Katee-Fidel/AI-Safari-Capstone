"""
agent_pride/agents.py
=====================
CrewAI agent definitions for Node 3 (The Hunt Catalyst).

Two agents:
  - MarketAnalystAgent   : Queries the local AssetOpportunity catalogue
                           using a safe Django ORM tool.
  - AllocationArchitect  : Drafts an optimised allocation strategy by
                           matching the execution intent to the analyst's
                           findings.

Security constraints:
  - No external network calls inside tools.
  - Agents receive only non-PII execution intent strings.
  - Tools are deterministic ORM queries with hard row-count limits.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from crewai import Agent, LLM
from crewai.tools import BaseTool
from django.conf import settings
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def build_configured_llm() -> LLM:
    """Build the CrewAI LLM from Django settings."""
    model = getattr(settings, "LLM_MODEL", "") or "gpt-4o-mini"
    provider = getattr(settings, "LLM_PROVIDER", "") or ""
    api_key = (
        getattr(settings, "LLM_API_KEY", "")
        or getattr(settings, "OPENAI_API_KEY", "")
        or None
    )
    base_url = (
        getattr(settings, "LLM_BASE_URL", "")
        or getattr(settings, "OPENAI_API_BASE", "")
        or None
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "timeout": 120,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if provider.lower() == "groq":
        kwargs["model"] = model.removeprefix("groq/")
        kwargs["provider"] = "openai"

    return LLM(**kwargs)


# ---------------------------------------------------------------------------
# Custom Tool: Asset Opportunity Fetcher
# ---------------------------------------------------------------------------


class AssetQueryInput(BaseModel):
    """Input schema for the AssetOpportunityFetcherTool."""

    asset_class_filter: str = Field(
        description=(
            "Asset class to filter by. Must be one of: MMF, SACCO_DEPOSIT, "
            "TREASURY_BILL, UNIT_TRUST_EQUITY, GOVERNMENT_BOND, CHAMA_CONTRIBUTION. "
            "Pass 'ALL' to retrieve across all classes."
        )
    )
    risk_tier: str = Field(
        description=(
            "Risk tier of the target profile. One of: CONSERVATIVE, MODERATE, "
            "GROWTH, AGGRESSIVE."
        )
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of asset records to return (1–10).",
    )


class AssetOpportunityFetcherTool(BaseTool):
    """
    Deterministic Django ORM tool that retrieves active asset opportunities
    from the local PostgreSQL database.

    Never calls external APIs. Returns a JSON string safe for agent consumption.
    """

    name: str = "asset_opportunity_fetcher"
    description: str = (
        "Fetches active local asset opportunities (MMF, Sacco, T-Bills, etc.) "
        "from the internal database. Filter by asset class and risk tier. "
        "Returns a JSON list of opportunities with yield, liquidity, and risk data."
    )
    args_schema: type[BaseModel] = AssetQueryInput

    def _run(
        self,
        asset_class_filter: str,
        risk_tier: str,
        max_results: int = 5,
    ) -> str:
        """
        Execute a safe, read-only ORM query against AssetOpportunity.

        Returns:
            JSON string containing a list of opportunity dicts, or an
            error message string if the query fails.
        """
        # Import here to avoid circular imports at module load time.
        from .models import AssetOpportunity  # noqa: PLC0415

        try:
            qs = AssetOpportunity.objects.filter(is_active=True)

            if asset_class_filter.upper() != "ALL":
                qs = qs.filter(asset_class=asset_class_filter.upper())

            # Filter by risk tier compatibility (PostgreSQL ArrayField contains)
            qs = qs.filter(suitable_risk_tiers__contains=[risk_tier.upper()])

            # Order by yield descending; cap at max_results
            qs = qs.order_by("-annualised_yield_pct")[:max_results]

            results: list[dict[str, Any]] = [
                {
                    "opportunity_id": str(opp.opportunity_id),
                    "provider_name": opp.provider_name,
                    "asset_class": opp.asset_class,
                    "annualised_yield_pct": float(opp.annualised_yield_pct),
                    "minimum_investment_kes": float(opp.minimum_investment_kes),
                    "liquidity_days": opp.liquidity_days,
                    "default_risk_pct": float(opp.default_risk_pct),
                    "max_leverage_ratio": float(opp.max_leverage_ratio),
                }
                for opp in qs
            ]

            logger.info(
                "AssetOpportunityFetcherTool returned %d records "
                "(filter=%s, tier=%s).",
                len(results),
                asset_class_filter,
                risk_tier,
            )
            return json.dumps(results, indent=2)

        except Exception as exc:  # pragma: no cover
            logger.exception("AssetOpportunityFetcherTool ORM query failed.")
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Custom Tool: Risk Metrics Summariser
# ---------------------------------------------------------------------------


class RiskMetricsInput(BaseModel):
    """Input schema for the RiskMetricsSummariserTool."""

    asset_class: str = Field(
        description="Asset class to summarise risk for."
    )


class RiskMetricsSummariserTool(BaseTool):
    """
    Returns aggregate risk statistics for a given asset class from the
    local database — no external data sources.
    """

    name: str = "risk_metrics_summariser"
    description: str = (
        "Summarises average default risk, average yield, and minimum liquidity "
        "for all active instruments in a given asset class. "
        "Use this before making allocation recommendations."
    )
    args_schema: type[BaseModel] = RiskMetricsInput

    def _run(self, asset_class: str) -> str:
        """
        Aggregate risk metrics for the requested asset class.

        Returns:
            JSON string with summary statistics.
        """
        from django.db.models import Avg, Min  # noqa: PLC0415

        from .models import AssetOpportunity  # noqa: PLC0415

        try:
            agg = AssetOpportunity.objects.filter(
                is_active=True,
                asset_class=asset_class.upper(),
            ).aggregate(
                avg_yield=Avg("annualised_yield_pct"),
                avg_default_risk=Avg("default_risk_pct"),
                min_liquidity_days=Min("liquidity_days"),
            )

            summary = {
                "asset_class": asset_class.upper(),
                "avg_annualised_yield_pct": round(float(agg["avg_yield"] or 0), 2),
                "avg_default_risk_pct": round(float(agg["avg_default_risk"] or 0), 2),
                "min_liquidity_days": agg["min_liquidity_days"] or 0,
            }

            logger.debug("RiskMetricsSummariserTool result: %s", summary)
            return json.dumps(summary, indent=2)

        except Exception as exc:  # pragma: no cover
            logger.exception("RiskMetricsSummariserTool failed.")
            return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Agent factory functions
# ---------------------------------------------------------------------------


def build_market_analyst_agent() -> Agent:
    """
    Construct the Market Analyst Agent.

    Responsibilities:
    - Query the local asset catalogue using ORM tools.
    - Summarise yield, liquidity, and default risk metrics.
    - Produce a structured market brief for the Allocation Architect.

    Returns:
        Configured CrewAI Agent instance.
    """
    llm = build_configured_llm()
    return Agent(
        role="Market Analyst",
        goal=(
            "Identify the top locally available asset opportunities "
            "that match the provided risk tier and income pattern. "
            "Produce a concise, data-backed market brief ranking candidates "
            "by risk-adjusted yield, liquidity, and minimum investment threshold."
        ),
        backstory=(
            "You are a senior analyst at a Nairobi-based wealth management firm "
            "with deep expertise in Kenyan capital markets — M-Pesa savings products, "
            "CMA-regulated unit trusts, SACCO deposits, and CBK Treasury instruments. "
            "You rely exclusively on verified internal data; you never speculate "
            "beyond what the database provides."
        ),
        tools=[
            AssetOpportunityFetcherTool(),
            RiskMetricsSummariserTool(),
        ],
        llm=llm,
        function_calling_llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=4,
        memory=False,  # Stateless — all state lives in the pipeline schemas.
    )


def build_allocation_architect_agent() -> Agent:
    """
    Construct the Allocation Architect Agent.

    Responsibilities:
    - Receive the Market Analyst's brief and the user's execution intent.
    - Draft a concrete, percentage-based allocation strategy across 1–5
      asset classes.
    - Allocations must sum to exactly 100%.
    - Output must be a valid JSON string matching CrewOutputSchema.

    Returns:
        Configured CrewAI Agent instance.
    """
    llm = build_configured_llm()
    return Agent(
        role="Allocation Architect",
        goal=(
            "Design an optimal, 100%-summing asset allocation strategy that "
            "precisely matches the client's income pattern, risk tier, liquidity "
            "preference, and investment horizon. "
            "Your final output MUST be a single valid JSON object containing "
            "'recommendations' (list) and 'chain_of_thought_md' (string). "
            "Each recommendation must include: asset_class, allocation_pct, "
            "expected_yield_pct, provider_name, and rationale."
        ),
        backstory=(
            "You are a certified financial planner and portfolio construction "
            "specialist. You have engineered allocation frameworks for Kenya's "
            "top three commercial banks and two leading fund managers. "
            "You are meticulous about regulatory compliance, portfolio diversification, "
            "and matching product suitability to client profiles. "
            "You never recommend products you have not verified through the analyst's data."
        ),
        tools=[],  # Architect reasons only — no direct DB access needed.
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3,
        memory=False,
    )
