"""
agent_pride/tasks.py
====================
CrewAI Task definitions for Node 3 (The Hunt Catalyst).

Tasks are constructed at runtime (not module level) so they receive
a fresh ExecutionIntentSchema context per pipeline invocation.
This prevents state bleed between concurrent requests.
"""

from __future__ import annotations

from crewai import Agent, Task

from .schemas import ExecutionIntentSchema
# ---------------------------------------------------------------------------
# Task factory functions
# ---------------------------------------------------------------------------


def build_market_research_task(
    agent: Agent,
    intent: ExecutionIntentSchema,
) -> Task:
    """
    Construct the Market Research Task assigned to the Market Analyst Agent.

    The task description contains only non-PII execution intent fields —
    risk tier, income pattern, preferred asset classes, and liquidity
    preference. No personal identifiers are included.

    Args:
        agent: The Market Analyst Agent instance.
        intent: The anonymised execution intent from Node 2.

    Returns:
        Configured CrewAI Task.
    """
    preferred_classes = ", ".join(cls.value for cls in intent.preferred_asset_classes)

    task_description = f"""
You are performing a market research assignment for an anonymised client profile.

**Client Profile (PII-FREE):**
- Income Pattern:       {intent.income_pattern.value}
- Risk Tier:           {intent.risk_tier.value}
- Liquidity Preference: {intent.liquidity_preference:.0%} of portfolio must remain liquid
- Investment Horizon:   {intent.horizon_months} months
- Investable Capacity:  Score {intent.investable_capacity_score:.1f}/100
- Preferred Asset Classes: {preferred_classes}

**Your Tasks:**
1. Use the `asset_opportunity_fetcher` tool to retrieve active opportunities for EACH
   preferred asset class listed above, filtered by risk tier `{intent.risk_tier.value}`.
2. Use the `risk_metrics_summariser` tool to gather aggregate risk statistics for each
   asset class you find relevant.
3. Compile a concise **Market Brief** in Markdown that:
   - Lists the top 3-5 specific products found (provider name, yield %, liquidity days,
     default risk %).
   - Ranks them by risk-adjusted suitability for the client profile.
   - Flags any product whose default risk exceeds 3% as HIGH RISK.
   - Notes whether minimum investment thresholds are achievable given
     investable capacity score {intent.investable_capacity_score:.1f}/100.

Your output will be consumed directly by the Allocation Architect — be precise and
structured. Do not include any personal data or speculation beyond the database records.
""".strip()

    return Task(
        description=task_description,
        expected_output=(
            "A structured Markdown market brief listing 3-5 ranked asset opportunities "
            "with yield, liquidity, default risk, and suitability notes. "
            "Each entry should clearly show provider name, asset class, yield %, "
            "liquidity days, and a risk flag."
        ),
        agent=agent,
    )


def build_allocation_strategy_task(
    agent: Agent,
    intent: ExecutionIntentSchema,
    context_tasks: list[Task],
) -> Task:
    """
    Construct the Allocation Strategy Task assigned to the Allocation Architect Agent.

    This task depends on (and consumes output from) the Market Research Task.
    It must produce a single, well-formed JSON string that maps directly to
    CrewOutputSchema.

    Args:
        agent: The Allocation Architect Agent instance.
        intent: The anonymised execution intent from Node 2.
        context_tasks: List of upstream tasks whose output is passed as context
                       (should contain the Market Research Task).

    Returns:
        Configured CrewAI Task.
    """
    preferred_classes = ", ".join(cls.value for cls in intent.preferred_asset_classes)
    illiquid_budget_pct = (1 - intent.liquidity_preference) * 100

    task_description = f"""
Using the Market Analyst's research brief (provided in context), design an optimal
asset allocation strategy for the following anonymised client profile.

**Client Profile (PII-FREE):**
- Income Pattern:       {intent.income_pattern.value}
- Risk Tier:           {intent.risk_tier.value}
- Liquidity Preference: {intent.liquidity_preference:.0%} liquid minimum
- Investment Horizon:   {intent.horizon_months} months
- Investable Capacity:  Score {intent.investable_capacity_score:.1f}/100
- Preferred Asset Classes: {preferred_classes}

**Allocation Rules (NON-NEGOTIABLE):**
1. All `allocation_pct` values MUST sum to exactly 100.0 (+-0.5 rounding tolerance).
2. The combined allocation to assets with `liquidity_days > 90` must NOT exceed
   {illiquid_budget_pct:.0f}% of the portfolio.
3. No single asset class may exceed 60% of the total allocation.
4. Only use products explicitly mentioned in the Market Analyst's brief.
5. Do NOT invent providers or yields not present in the brief.

**Required Output Format:**
You MUST respond with a single raw JSON object (no Markdown code fences, no preamble).
The JSON must strictly follow this structure:

{{
  "recommendations": [
    {{
      "asset_class": "<AssetClass enum value>",
      "allocation_pct": <float 0-100>,
      "expected_yield_pct": <float 0-25>,
      "provider_name": "<string>",
      "rationale": "<1-2 sentence justification>"
    }}
  ],
  "chain_of_thought_md": "<Full reasoning trace as a SINGLE LINE string. Use literal \\n for line breaks. Do NOT use actual newlines inside this string value.>"
}}

The `chain_of_thought_md` field must describe:
- Why each asset class was chosen or excluded.
- How liquidity constraints shaped the allocation.
- How the income pattern informed weighting decisions.
- Any risk trade-offs acknowledged.
""".strip()

    return Task(
        description=task_description,
        expected_output=(
            "A single raw JSON object (no code fences) with keys 'recommendations' "
            "(list of allocation dicts, summing to 100%) and 'chain_of_thought_md' "
            "(Markdown string with full reasoning). "
            "Each recommendation must include: asset_class, allocation_pct, "
            "expected_yield_pct, provider_name, rationale."
        ),
        agent=agent,
        context=context_tasks,
    )