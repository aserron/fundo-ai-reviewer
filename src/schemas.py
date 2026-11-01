"""Strict Pydantic schemas and domain invariants for Fundo AI Reviewer."""

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class FundoGroup(str, Enum):
    """The 13 Fundo Keyword Groups + None."""
    NOT_AVERAGE_MONTHLY_REVENUE = "Not average monthly revenue"
    NSFS = "NSFs"
    OVERDRAFT = "Overdraft"
    INTERNAL_TRANSFER = "Internal transfer"
    UCC = "UCC"
    ACTIVE_ADVANCE = "Active advance"
    AUTO_DEPOSIT = "Auto deposit"
    REVENUE_VERIFICATION = "Revenue verification"
    HIGH_RISK_GAMBLING = "High risk — gambling"
    HIGH_RISK_BANKRUPTCY = "High risk — bankruptcy"
    HIGH_RISK_DEBT_SETTLEMENT = "High risk — debt settlement payments"
    HIGH_RISK_GARNISHMENT = "High risk — garnishment"
    HIGH_RISK_OTHER = "High risk — other"
    NONE = "None"


EXCLUDED_REVENUE_GROUPS = {
    FundoGroup.NOT_AVERAGE_MONTHLY_REVENUE,
    FundoGroup.INTERNAL_TRANSFER,
    FundoGroup.ACTIVE_ADVANCE,
    FundoGroup.NSFS,
    FundoGroup.OVERDRAFT,
    FundoGroup.UCC,
    FundoGroup.HIGH_RISK_GAMBLING,
    FundoGroup.HIGH_RISK_BANKRUPTCY,
    FundoGroup.HIGH_RISK_DEBT_SETTLEMENT,
    FundoGroup.HIGH_RISK_GARNISHMENT,
    FundoGroup.HIGH_RISK_OTHER,
}


def compute_is_revenue(
    amount: float,
    is_business: bool,
    group: FundoGroup | str | None,
) -> bool:
    """Enforce the strict revenue invariant in deterministic code.

    A transaction has revenue = True if and only if:
    1. amount < 0 (credit/inflow in Plaid convention)
    2. is_business == True
    3. group is NOT in EXCLUDED_REVENUE_GROUPS
    """
    if amount >= 0.0:
        return False
    if not is_business:
        return False
    if group is None:
        return True
    
    # Normalize group string if needed
    if isinstance(group, str):
        try:
            group = FundoGroup(group)
        except ValueError:
            pass

    return group not in EXCLUDED_REVENUE_GROUPS


class GroundTruthLabel(BaseModel):
    group: FundoGroup
    is_business: bool
    is_revenue: bool
    notes: str = ""


class PlaidTransaction(BaseModel):
    transaction_id: str
    business_id: str
    date: str
    name: str
    amount: float  # Plaid sign: < 0 is credit/inflow, > 0 is debit/outflow
    category: List[str] = Field(default_factory=list)
    pending: bool = False
    account_id: Optional[str] = None
    ground_truth: Optional[GroundTruthLabel] = Field(default=None, alias="_ground_truth")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def is_credit(self) -> bool:
        return self.amount < 0.0

    @property
    def is_debit(self) -> bool:
        return self.amount > 0.0

    @property
    def abs_amount(self) -> float:
        return abs(self.amount)


class LegacyLabel(BaseModel):
    transaction_id: str
    group: FundoGroup
    is_business: bool
    is_revenue: bool
    confidence: float = 1.0
    matched_groups: List[FundoGroup] = Field(default_factory=list)
    matched_keywords: List[str] = Field(default_factory=list)


class ReviewerProposal(BaseModel):
    group: FundoGroup
    is_business: bool
    confidence: float
    reason: str


class ReviewFlag(BaseModel):
    transaction_id: str
    business_id: str
    original_group: FundoGroup
    proposed_group: FundoGroup
    is_business: bool
    is_revenue: bool
    confidence: float
    reason: str
    dollar_impact: float = 0.0
    is_flagged: bool = True


class BusinessProfile(BaseModel):
    business_id: str
    name: str
    industry: str
    bank_charges_nsf_fees: bool = True
    window_days: int = 90


class BusinessMetrics(BaseModel):
    business_id: str
    state: str  # "legacy", "reviewed", "ground_truth"
    total_credits: float
    total_debits: float
    avg_monthly_revenue: float
    revenue_share_of_deposits: float
    nsf_overdraft_count: int
    other_funders_daily_payment: float
    calculated_offer: float
    monthly_revenue_dollar_error: float = 0.0
    offer_dollar_error: float = 0.0
    window_coverage_days: int = 90
    is_truncated_window: bool = False
