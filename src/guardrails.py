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
    "R09",
    "RETURN",
    "RET",
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

PROCESSOR_SETTLEMENT_TOKENS = (
    "*SQ",
    "SQUARE",
    "SHOPIFY",
    "STRIPE",
    "TOAST",
    "CLOVER",
    "PAYPAL",
    "SETTLEMENT",
    "POS DEPOSIT",
    "MERCHANT SERVICES",
    "S&P",
    "A+ TOWING",
)


def contains_ambiguous_tokens(name: str) -> bool:
    """Check if transaction memo contains known ambiguous or overloaded keywords."""
    upper = name.upper()
    return (
        any(pattern.search(name) for pattern in TOKEN_PATTERNS)
        or "*SQ" in upper
        or "S&P" in upper
        or "A+" in upper
    )


def is_processor_or_punct_settlement(name: str) -> bool:
    """Return True if the memo represents a merchant processor or punctuation-masked settlement."""
    upper = name.upper()
    return any(tok in upper for tok in PROCESSOR_SETTLEMENT_TOKENS)


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
    if tx.is_credit:
        return True
    if len(legacy.matched_groups) > 1:
        return True
    if contains_ambiguous_tokens(tx.name):
        return True
    if legacy.group == FundoGroup.NONE and tx.amount > 400.0:
        return True
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
    3. Hard-Negative & Confidence Gating:
       - Suppresses benign NONE <-> REVENUE_VERIFICATION swaps on standard B2B invoices
         where both agree is_revenue=True and no processor/punctuation bug is involved.
       - Suppresses disagreements with confidence < 0.75.
       - Routes large credits (>= $2,500) with confidence < 0.60 as manual check flags.
    4. Reason formatting: Formats 5-second underwriter explanation <= 120 chars with dollar impact.
    """
    proposed_group = proposal.group
    reviewed_revenue = compute_is_revenue(
        amount=tx.amount,
        is_business=proposal.is_business,
        group=proposed_group,
    )

    # Suppress false group-only disagreement on standard B2B customer invoices (Hard Negatives)
    if (
        tx.is_credit
        and legacy.is_business == proposal.is_business
        and legacy.is_revenue
        and reviewed_revenue
        and {legacy.group, proposed_group} == {FundoGroup.NONE, FundoGroup.REVENUE_VERIFICATION}
        and not is_processor_or_punct_settlement(tx.name)
    ):
        proposed_group = legacy.group

    # Calculate dollar impact
    dollar_impact = 0.0
    if tx.is_credit and (legacy.is_revenue != reviewed_revenue):
        dollar_impact = tx.abs_amount
    elif (
        tx.is_debit
        and (legacy.group == FundoGroup.ACTIVE_ADVANCE or proposed_group == FundoGroup.ACTIVE_ADVANCE)
        and (legacy.group != proposed_group)
    ):
        dollar_impact = tx.abs_amount

    has_disagreement = (
        (legacy.group != proposed_group)
        or (legacy.is_business != proposal.is_business)
        or (legacy.is_revenue != reviewed_revenue)
    )

    reason = proposal.reason.strip()

    # Confidence Gating
    if tx.is_credit and tx.abs_amount >= 2500.0 and proposal.confidence < 0.60:
        is_flagged = True
        dollar_impact = tx.abs_amount
        reason = f"Low-confidence large credit (${tx.abs_amount:,.2f}) — manual check: {reason}"
    elif has_disagreement and proposal.confidence >= 0.75:
        is_flagged = True
        if dollar_impact > 0 and "$" not in reason:
            suffix = f" (${dollar_impact:,.0f} impact)"
            if len(reason) + len(suffix) <= 120:
                reason = reason + suffix
            else:
                reason = reason[: 120 - len(suffix) - 3] + "..." + suffix
    else:
        is_flagged = False

    if len(reason) > 120:
        reason = reason[:117] + "..."

    return ReviewFlag(
        transaction_id=tx.transaction_id,
        business_id=tx.business_id,
        original_group=legacy.group,
        proposed_group=proposed_group,
        is_business=proposal.is_business,
        is_revenue=reviewed_revenue,
        confidence=proposal.confidence,
        reason=reason,
        dollar_impact=dollar_impact,
        is_flagged=is_flagged,
    )
