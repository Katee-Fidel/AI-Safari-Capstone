from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel


class AssetClass(str, Enum):
    MMF = "MMF"
    SACCO_DEPOSIT = "SACCO_DEPOSIT"
    TREASURY_BILL = "TREASURY_BILL"
    UNIT_TRUST_EQUITY = "UNIT_TRUST_EQUITY"
    GOVERNMENT_BOND = "GOVERNMENT_BOND"
    CHAMA_CONTRIBUTION = "CHAMA_CONTRIBUTION"


class IncomePatternClass(str, Enum):
    CHAMA_POOLED = "CHAMA_POOLED"
    HIGH_VELOCITY_LIQUID = "HIGH_VELOCITY_LIQUID"
    IRREGULAR_GIG = "IRREGULAR_GIG"
    LOW_VELOCITY_ACCUMULATION = "LOW_VELOCITY_ACCUMULATION"
    MEDIUM_VELOCITY_MIXED = "MEDIUM_VELOCITY_MIXED"


class RiskTier(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    MODERATE = "MODERATE"
    GROWTH = "GROWTH"
    AGGRESSIVE = "AGGRESSIVE"


class PipelineStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"


class AnonymisedTelemetrySchema(BaseModel):
    session_id: uuid.UUID
    balance_tier: int
    velocity_score: float
    frequency_multiplier: float
    avg_transaction_size_tier: int
    chama_participation_flag: bool = False
    chama_contribution_tier: int = 0
    mpesa_active_days_last_30: int
    bill_payment_regularity: float
    source_channel: str = "api"


class AssetRecommendationSchema(BaseModel):
    asset_class: AssetClass
    allocation_pct: float
    expected_yield_pct: float
    provider_name: str
    rationale: str


class ComplianceCheckSchema(BaseModel):
    rule_id: str
    description: str
    passed: bool
    observed_value: float
    threshold: float


class ExecutionIntentSchema(BaseModel):
    session_id: uuid.UUID
    income_pattern: IncomePatternClass
    risk_tier: RiskTier
    investable_capacity_score: float
    preferred_asset_classes: list[AssetClass]
    liquidity_preference: float
    horizon_months: int


class CrewOutputSchema(BaseModel):
    session_id: uuid.UUID
    crew_run_duration_seconds: float
    recommendations: list[AssetRecommendationSchema]
    chain_of_thought_md: str


class VerifiedAllocationSchema(BaseModel):
    session_id: uuid.UUID
    final_recommendations: list[AssetRecommendationSchema]
    compliance_checks: list[ComplianceCheckSchema]
    fallback_applied: bool
    overall_default_risk_pct: float
    max_leverage_ratio: float
    sentinel_verdict: str


class LedgerEntrySchema(BaseModel):
    ledger_id: uuid.UUID
    session_id: uuid.UUID
    status: PipelineStatus
    income_pattern: IncomePatternClass
    risk_tier: RiskTier
    fallback_applied: bool
    overall_default_risk_pct: float
    final_recommendations: list[AssetRecommendationSchema]
    compliance_checks: list[ComplianceCheckSchema]
    chain_of_thought_md: str
    sentinel_verdict: str
    crew_run_duration_seconds: float
