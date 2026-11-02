"""Legacy 13-Group Keyword Engine with realistic production flaws and precedence rules."""

import re
from typing import Dict, List, Optional, Tuple
from src.schemas import FundoGroup, LegacyLabel, PlaidTransaction, compute_is_revenue

# Strict legacy precedence order
LEGACY_PRECEDENCE: List[FundoGroup] = [
    FundoGroup.NOT_AVERAGE_MONTHLY_REVENUE,
    FundoGroup.NSFS,
    FundoGroup.OVERDRAFT,
    FundoGroup.INTERNAL_TRANSFER,
    FundoGroup.UCC,
    FundoGroup.ACTIVE_ADVANCE,
    FundoGroup.AUTO_DEPOSIT,
    FundoGroup.REVENUE_VERIFICATION,
    FundoGroup.HIGH_RISK_GAMBLING,
    FundoGroup.HIGH_RISK_BANKRUPTCY,
    FundoGroup.HIGH_RISK_DEBT_SETTLEMENT,
    FundoGroup.HIGH_RISK_GARNISHMENT,
    FundoGroup.HIGH_RISK_OTHER,
]

# Raw keyword dictionaries configured in legacy system
LEGACY_GROUP_KEYWORDS: Dict[FundoGroup, List[str]] = {
    FundoGroup.NOT_AVERAGE_MONTHLY_REVENUE: [
        "OWNER CAPITAL",
        "CAPITAL CONTRIBUTION",
        "TAX REFUND",
        "IRS TREAS",
        "INSURANCE PROCEEDS",
        "ASSET SALE",
        "SBA DISBURSEMENT",
        "EIDL ADVANCE",
    ],
    FundoGroup.NSFS: [
        "NSF FEE",
        "RETURNED ITEM FEE",
        "INSUFFICIENT FUNDS",
        "RETURN ITEM CHARGE",
        "UNPAID ITEM",
    ],
    FundoGroup.OVERDRAFT: [
        "OVERDRAFT FEE",
        "OVERDRAFT ITEM",
        "DAILY OD FEE",
        "COURTESY PAY FEE",
    ],
    FundoGroup.INTERNAL_TRANSFER: [
        "TRANSFER",
        "XFER",
        "INTERNET TRANSFER",
        "ONLINE TRANSFER",
        "SWEEP",
        "BOOK TRANSFER",
        "ZELLE TO SELF",
    ],
    FundoGroup.UCC: [
        "UCC FILING",
        "UCC-1",
        "SECRETARY OF STATE FILING",
        "CSC UCC",
        "CSC*UCC",  # Flawed boundary
    ],
    FundoGroup.ACTIVE_ADVANCE: [
        "ONDECK",
        "CREDIBLY",
        "FORWARD FINANCING",
        "LIBERTAS",
        "YELLOWSTONE",
        "CAN CAPITAL",
        "KABBAGE",
        "BIZFI",
        "MERCHANT CASH",
        "SPLIT SETTLEMENT",
    ],
    FundoGroup.AUTO_DEPOSIT: [
        "AUTO DEPOSIT",
        "AUTOMATIC DEPOSIT",
        "REMOTE ONLINE DEPOSIT",
        "MOBILE CHECK DEPOSIT",
        "ATM DEPOSIT",
    ],
    FundoGroup.REVENUE_VERIFICATION: [
        "SHOPIFY",
        "STRIPE",
        "SQUARE",
        "*SQ",  # Flawed boundary: leading punctuation fails on naive \b*SQ\b
        "S&P",
        "A+",   # Flawed boundary: trailing punctuation fails on naive \bA\+\b
        "TOAST",
        "CLOVER",
        "MERCHANT SERVICES",
        "SETTLEMENT",
        "POS DEPOSIT",
        "PAYPAL",
    ],
    FundoGroup.HIGH_RISK_GAMBLING: [
        "DRAFTKINGS",
        "FANDUEL",
        "BETMGM",
        "CASINO",
        "POKER",
        "CAESARS",
    ],
    FundoGroup.HIGH_RISK_BANKRUPTCY: [
        "BANKRUPTCY",
        "CHAPTER 11",
        "CHAPTER 7",
        "TRUSTEE",
        "PACER",
    ],
    FundoGroup.HIGH_RISK_DEBT_SETTLEMENT: [
        "DEBT RELIEF",
        "DEBT SETTLEMENT",
        "FREEDOM DEBT",
        "NATIONAL DEBT",
        "CLEARONE",
    ],
    FundoGroup.HIGH_RISK_GARNISHMENT: [
        "LEVY",
        "GARNISHMENT",
        "TAX LIEN",
        "IRS LEVY",
        "USATAXPYMT LEVY",
    ],
    FundoGroup.HIGH_RISK_OTHER: [
        "EZ PAWN",
        "PAWN SHOP",
        "PAYDAY",
        "BAIL BONDS",
        "CRYPTO OFFRAMP",
    ],
}

PERSONAL_KEYWORDS: List[str] = [
    "NETFLIX",
    "SPOTIFY",
    "WHOLE FOODS",
    "EQUINOX",
    "LOUIS VUITTON",
    "ATM WITHDRAWAL",
    "PERSONAL CHECK",
]


class LegacyKeywordEngine:
    """The imperfect legacy keyword engine described in the challenge.

    Implements:
    1. Precedence ordering across the 13 groups.
    2. Naive word-boundary regex compilation r"\\b" + re.escape(kw) + r"\\b",
       which silently fails when keywords contain leading/trailing punctuation.
    3. Collision tracking (all matching groups vs winning group).
    4. Personal expense detection setting is_business = False.
    """

    def __init__(self):
        # Compile patterns with naive word boundaries
        self.compiled_rules: Dict[FundoGroup, List[Tuple[str, re.Pattern]]] = {}
        for group, keywords in LEGACY_GROUP_KEYWORDS.items():
            self.compiled_rules[group] = [
                (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
                for kw in keywords
            ]

        self.personal_patterns = [
            (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
            for kw in PERSONAL_KEYWORDS
        ]

    def label_transaction(self, tx: PlaidTransaction) -> LegacyLabel:
        """Classify a transaction using legacy rules and precedence."""
        name = tx.name
        matched_groups: List[FundoGroup] = []
        matched_keywords: List[str] = []

        # Check all group patterns to record collisions
        for group in LEGACY_PRECEDENCE:
            for kw, pattern in self.compiled_rules[group]:
                if pattern.search(name):
                    if group not in matched_groups:
                        matched_groups.append(group)
                    matched_keywords.append(kw)

        # Winning group is chosen by strict precedence order
        winning_group = FundoGroup.NONE
        for group in LEGACY_PRECEDENCE:
            if group in matched_groups:
                winning_group = group
                break

        # Check personal patterns
        is_business = True
        for kw, pattern in self.personal_patterns:
            if pattern.search(name):
                is_business = False
                matched_keywords.append(f"PERSONAL:{kw}")
                break

        # Apply deterministic revenue logic
        is_revenue = compute_is_revenue(tx.amount, is_business, winning_group)

        return LegacyLabel(
            transaction_id=tx.transaction_id,
            group=winning_group,
            is_business=is_business,
            is_revenue=is_revenue,
            confidence=1.0 if winning_group != FundoGroup.NONE else 0.5,
            matched_groups=matched_groups,
            matched_keywords=matched_keywords,
        )
