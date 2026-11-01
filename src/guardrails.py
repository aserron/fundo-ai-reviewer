"""Deterministic pre-LLM triage gate and post-LLM financial invariants."""

import re
from typing import Set
from src.schemas import FundoGroup, LegacyLabel, PlaidTransaction, ReviewFlag, ReviewerProposal, compute_is_revenue

# Ambiguous/overloaded bank memo tokens requiring review
AMBIGUOUS_TOKENS: Set[str] = {
    "SQUARE",
    "SQ",
    "SHOPIFY",
    "STRIPE",
    "PAYPAL",
    "TRANSFER",
    "CAPITAL",
    "ADVANCE",
    "FUNDING",
    "R01",
    "RETURN",
    "NSF",
    "OVERDRAFT",
    "OD",
    "LEVY",
    "UCC",
    "RELIEF",
    "TRUSTEE",
    "ZELLE",
    "VENMO",
}

TOKEN_PATTERNS = [re.compile(rf"(?i)\b{re.escape(tok)}\b") for tok in AMBIGUOUS_TOKENS]


def contains_ambiguous_tokens(name: str) -> bool:
    """Check if transaction memo contains known ambiguous or overloaded keywords."""
    return any(pattern.search(name) for pattern in TOKEN_PATTERNS) or "*SQ" in name.upper()


def should_route_to_llm(
    tx: PlaidTransaction,
    legacy: LegacyLabel,
    is_recurring: bool = False,
) -> bool:
    """Deterministic Pre-LLM Triage Gate.

    Routes a transaction to the LLM reviewer if and only if:
    1. tx.amount < 0 (all credits are audited due to asymmetric revenue dollar risk).
    2. len(legacy.matched_groups) > 1 (legacy rule collisions).
    3. tx.name contains ambiguous/overloaded tokens.
    4. tx.amount > 400.0 on an unclassified debit (legacy.group == None).
    5. is_recurring is True (potential disguised MCA daily debits).
    """
    # 1. Audit all credits
    if tx.is_credit:
        return True

    # 2. Rule collisions
    if len(legacy.matched_groups) > 1:
        return True

    # 3. Ambiguous tokens
    if contains_ambiguous_tokens(tx.name):
        return True

    # 4. Large unclassified debit
    if legacy.group == FundoGroup.NONE and tx.amount > 400.0:
        return True

    # 5. Recurring debit patterns
    if is_recurring:
        return True

    return False


def apply_post_guardrails(
    tx: PlaidTransaction,
    legacy: LegacyLabel,
    proposal: ReviewerProposal,
) -> ReviewFlag:
    """Deterministic Post-LLM Financial Invariants.

    Guarantees:
    1. Sign Lock: Debits (amount >= 0) can NEVER be marked as revenue,
       even if the LLM output claims revenue=True.
    2. Formula Lock: Revenue calculation is re-evaluated strictly in Python.
    3. Confidence Gating: Disagreements with confidence < 0.75 are suppressed
       to prevent underwriter fatigue on hard negatives. Large credits (>= $2,500)
       with confidence < 0.60 are routed as 'Low-confidence large credit — manual check'.
    4. Reason formatting: Formats 5-second underwriter explanation <= 120 chars.
    """
    # 1 & 2: Sign and Formula Lock
    reviewed_revenue = compute_is_revenue(
        amount=tx.amount,
        is_business=proposal.is_business,
        group=proposal.group,
    )

    # Calculate dollar impact
    dollar_impact = 0.0
    if tx.is_credit and (legacy.is_revenue != reviewed_revenue):
        dollar_impact = tx.abs_amount
    elif (
        tx.is_debit
        and (legacy.group == FundoGroup.ACTIVE_ADVANCE or proposal.group == FundoGroup.ACTIVE_ADVANCE)
        and (legacy.group != proposal.group)
    ):
        dollar_impact = tx.abs_amount

    # Check for disagreement between legacy label and reviewer proposal
    has_disagreement = (
        (legacy.group != proposal.group)
        or (legacy.is_business != proposal.is_business)
        or (legacy.is_revenue != reviewed_revenue)
    )

    reason = proposal.reason.strip()

    # 3: Confidence Gating
    if tx.is_credit and tx.abs_amount >= 2500.0 and proposal.confidence < 0.60:
        is_flagged = True
        reason = f"Low-confidence large credit (${tx.abs_amount:,.2f}) — manual check: {reason}"
    elif has_disagreement and proposal.confidence >= 0.75:
        is_flagged = True
    else:
        is_flagged = False

    # 4: Reason truncation to <= 120 chars
    if len(reason) > 120:
        reason = reason[:117] + "..."

    return ReviewFlag(
        transaction_id=tx.transaction_id,
        business_id=tx.business_id,
        original_group=legacy.group,
        proposed_group=proposal.group,
        is_business=proposal.is_business,
        is_revenue=reviewed_revenue,
        confidence=proposal.confidence,
        reason=reason,
        dollar_impact=dollar_impact,
        is_flagged=is_flagged,
    )
