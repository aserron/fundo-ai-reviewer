"""Tests for the Legacy 13-Group Keyword Engine and its intentional production flaws."""

import pytest
from src.schemas import FundoGroup, PlaidTransaction
from src.keyword_engine import LegacyKeywordEngine


@pytest.fixture
def legacy_engine():
    return LegacyKeywordEngine()


class TestLegacyKeywordEngineFlaws:
    def test_flaw_1_noisy_bank_descriptions_transfer_collision(self, legacy_engine):
        """Noisy Shopify settlement description matches 'TRANSFER' and gets falsely labeled

        as 'Internal transfer' due to strict precedence over 'Revenue verification'.
        """
        tx = PlaidTransaction(
            transaction_id="tx_shopify_01",
            business_id="BIZ_02",
            date="2026-01-15",
            name="ORIG CO NAME:SHOPIFY CO ENTRY DESCR:TRANSFER",
            amount=-3500.00,
        )
        label = legacy_engine.label_transaction(tx)
        
        # In legacy engine, 'TRANSFER' triggers INTERNAL_TRANSFER before SHOPIFY triggers REVENUE_VERIFICATION
        assert label.group == FundoGroup.INTERNAL_TRANSFER
        assert not label.is_revenue  # False negative on legitimate revenue!
        assert FundoGroup.INTERNAL_TRANSFER in label.matched_groups
        assert FundoGroup.REVENUE_VERIFICATION in label.matched_groups
        assert len(label.matched_groups) > 1

    def test_flaw_2_counterparty_overload_square_capital_credit(self, legacy_engine):
        """Square Capital loan deposit matches 'SQUARE' and is mislabeled as Revenue verification

        instead of Not average monthly revenue, artificially inflating revenue.
        """
        tx = PlaidTransaction(
            transaction_id="tx_sq_loan_01",
            business_id="BIZ_01",
            date="2026-01-10",
            name="ACH CREDIT SQUARE CAPITAL LOAN PROCEEDS",
            amount=-25000.00,
        )
        label = legacy_engine.label_transaction(tx)
        # Naive keyword engine matches SQUARE -> Revenue verification, ignoring CAPITAL LOAN
        assert label.group == FundoGroup.REVENUE_VERIFICATION
        assert label.is_revenue  # False positive on loan proceeds!

    def test_flaw_3_silent_punctuation_failure(self, legacy_engine):
        """Naive word boundaries r'\\b' + kw + r'\\b' fail on leading punctuation like '*SQ'.

        'ACH CREDIT *SQ JOES TACOS' fails to match '*SQ' and falls through to None.
        """
        tx = PlaidTransaction(
            transaction_id="tx_sq_punct_01",
            business_id="BIZ_01",
            date="2026-01-12",
            name="ACH CREDIT *SQ JOES TACOS",
            amount=-1250.00,
        )
        label = legacy_engine.label_transaction(tx)
        # Because of r'\b*SQ\b', regex fails on leading asterisk and returns None
        assert label.group == FundoGroup.NONE

    def test_precedence_determinism(self, legacy_engine):
        """When multiple groups match, legacy fixed precedence wins."""
        # Contains NSF fee and gambling keywords
        tx = PlaidTransaction(
            transaction_id="tx_combo_01",
            business_id="BIZ_04",
            date="2026-01-20",
            name="NSF FEE DRAFTKINGS",
            amount=35.00,
        )
        label = legacy_engine.label_transaction(tx)
        assert label.group == FundoGroup.NSFS  # NSFS has higher precedence than HIGH_RISK_GAMBLING

    def test_personal_expense_classification(self, legacy_engine):
        """Personal counterparty flags is_business = False."""
        tx = PlaidTransaction(
            transaction_id="tx_personal_01",
            business_id="BIZ_03",
            date="2026-01-22",
            name="NETFLIX.COM SUBSCRIPTION",
            amount=22.99,
        )
        label = legacy_engine.label_transaction(tx)
        assert not label.is_business
        assert not label.is_revenue
