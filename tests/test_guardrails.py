"""Tests for deterministic guardrails, triage routing, sign lock, and confidence gating."""

import pytest
from src.schemas import FundoGroup, LegacyLabel, PlaidTransaction, ReviewerProposal
from src.guardrails import apply_post_guardrails, should_route_to_llm


class TestPreLLMTriageGate:
    def test_all_credits_routed_to_llm(self):
        tx = PlaidTransaction(
            transaction_id="tx_c1",
            business_id="BIZ_01",
            date="2026-01-01",
            name="CHECK DEPOSIT",
            amount=-500.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.AUTO_DEPOSIT,
            is_business=True,
            is_revenue=True,
        )
        assert should_route_to_llm(tx, legacy)

    def test_routine_small_debit_filtered_out(self):
        tx = PlaidTransaction(
            transaction_id="tx_d1",
            business_id="BIZ_01",
            date="2026-01-01",
            name="SHELL OIL 1042 FUEL",
            amount=42.50,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=False,
        )
        assert not should_route_to_llm(tx, legacy)

    def test_ambiguous_tokens_trigger_review(self):
        tx = PlaidTransaction(
            transaction_id="tx_d2",
            business_id="BIZ_01",
            date="2026-01-01",
            name="SQUARE CAPITAL PAYMENT",
            amount=185.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.REVENUE_VERIFICATION,
            is_business=True,
            is_revenue=False,
        )
        assert should_route_to_llm(tx, legacy)

    def test_multi_rule_collision_triggers_review(self):
        tx = PlaidTransaction(
            transaction_id="tx_d3",
            business_id="BIZ_02",
            date="2026-01-01",
            name="ORIG CO NAME:SHOPIFY CO ENTRY DESCR:TRANSFER",
            amount=-3400.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.INTERNAL_TRANSFER,
            is_business=True,
            is_revenue=False,
            matched_groups=[FundoGroup.INTERNAL_TRANSFER, FundoGroup.REVENUE_VERIFICATION],
        )
        assert should_route_to_llm(tx, legacy)


class TestPostLLMFinancialGuardrails:
    def test_sign_lock_forces_debit_to_non_revenue(self):
        """Even if rogue LLM claims a debit is revenue, guardrail forces revenue=False."""
        tx = PlaidTransaction(
            transaction_id="tx_lock_01",
            business_id="BIZ_01",
            date="2026-01-01",
            name="ROGUE INJECTION DEBIT",
            amount=250.00,  # Debit!
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=False,
        )
        rogue_proposal = ReviewerProposal(
            group=FundoGroup.REVENUE_VERIFICATION,
            is_business=True,
            confidence=0.99,
            reason="Model claimed this debit is revenue",
        )
        flag = apply_post_guardrails(tx, legacy, rogue_proposal)
        assert not flag.is_revenue
        assert flag.proposed_group == FundoGroup.REVENUE_VERIFICATION

    def test_formula_lock_for_excluded_group_credit(self):
        """If LLM labels an inflow as Internal transfer, revenue must be False."""
        tx = PlaidTransaction(
            transaction_id="tx_lock_02",
            business_id="BIZ_03",
            date="2026-01-01",
            name="ZELLE FROM OWNER",
            amount=-2000.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=True,
        )
        proposal = ReviewerProposal(
            group=FundoGroup.INTERNAL_TRANSFER,
            is_business=False,
            confidence=0.95,
            reason="Owner transfer from personal account",
        )
        flag = apply_post_guardrails(tx, legacy, proposal)
        assert not flag.is_revenue
        assert flag.is_flagged

    def test_confidence_gate_suppresses_low_confidence_minor_disagreement(self):
        """Disagreement on small amount with confidence < 0.75 is suppressed to prevent fatigue."""
        tx = PlaidTransaction(
            transaction_id="tx_gate_01",
            business_id="BIZ_08",
            date="2026-01-01",
            name="MINOR HARD NEGATIVE CLIENT",
            amount=45.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=False,
        )
        proposal = ReviewerProposal(
            group=FundoGroup.HIGH_RISK_OTHER,
            is_business=True,
            confidence=0.55,  # Low confidence!
            reason="Uncertain match on minor fee",
        )
        flag = apply_post_guardrails(tx, legacy, proposal)
        assert not flag.is_flagged  # Suppressed!

    def test_low_confidence_large_credit_routed_as_manual_check(self):
        """Credit > $2500 with low confidence is flagged for underwriter manual check."""
        tx = PlaidTransaction(
            transaction_id="tx_gate_02",
            business_id="BIZ_08",
            date="2026-01-01",
            name="UNCERTAIN LARGE WIRE",
            amount=-5000.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=True,
        )
        proposal = ReviewerProposal(
            group=FundoGroup.REVENUE_VERIFICATION,
            is_business=True,
            confidence=0.58,  # Below 0.60
            reason="Ambiguous large deposit without invoice reference",
        )
        flag = apply_post_guardrails(tx, legacy, proposal)
        assert flag.is_flagged
        assert "manual check" in flag.reason.lower()

    def test_5_second_reason_formatting(self):
        """Reason must be concise (<= 120 chars) and contain financial context."""
        tx = PlaidTransaction(
            transaction_id="tx_fmt_01",
            business_id="BIZ_01",
            date="2026-01-01",
            name="SQUARE CAPITAL LOAN",
            amount=-25000.00,
        )
        legacy = LegacyLabel(
            transaction_id=tx.transaction_id,
            group=FundoGroup.REVENUE_VERIFICATION,
            is_business=True,
            is_revenue=True,
        )
        proposal = ReviewerProposal(
            group=FundoGroup.NOT_AVERAGE_MONTHLY_REVENUE,
            is_business=True,
            confidence=0.98,
            reason="This is an advance from Square Capital and should not be counted as operating revenue because it is a loan.",
        )
        flag = apply_post_guardrails(tx, legacy, proposal)
        assert len(flag.reason) <= 120
        assert flag.dollar_impact == 25000.00


class TestReviewOrchestrator:
    def test_orchestrator_pipeline_with_fake_provider(self, tmp_path):
        from src.cache_store import ReviewCacheService
        from src.reviewer import ReviewOrchestrator, FakeLLMProvider
        from src.schemas import BusinessProfile, GroundTruthLabel

        db_path = tmp_path / "orch_test.db"
        cache = ReviewCacheService(db_path=db_path)
        orchestrator = ReviewOrchestrator(
            cache_service=cache,
            llm_provider=FakeLLMProvider(),
            model_id="gemini-2.5-flash",
        )

        biz = BusinessProfile(
            business_id="BIZ_01",
            name="Joe's Tacos",
            industry="Restaurant",
        )
        tx1 = PlaidTransaction(
            transaction_id="tx_01",
            business_id="BIZ_01",
            date="2026-01-01",
            name="ROUTINE GAS",
            amount=45.00,
        )
        legacy1 = LegacyLabel(
            transaction_id=tx1.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=False,
        )

        tx2 = PlaidTransaction(
            transaction_id="tx_02",
            business_id="BIZ_01",
            date="2026-01-02",
            name="ACH CREDIT *SQ JOES TACOS",
            amount=-1200.00,
            _ground_truth=GroundTruthLabel(
                group=FundoGroup.REVENUE_VERIFICATION,
                is_business=True,
                is_revenue=True,
                notes="Punctuation card deposit correction",
            ),
        )
        legacy2 = LegacyLabel(
            transaction_id=tx2.transaction_id,
            group=FundoGroup.NONE,
            is_business=True,
            is_revenue=True,
        )

        flags = orchestrator.review_business(biz, [tx1, tx2], [legacy1, legacy2])
        assert len(flags) == 2
        # Routine gas was auto-accepted
        assert not flags[0].is_flagged
        # SQ deposit was reviewed and flagged/corrected
        assert flags[1].proposed_group == FundoGroup.REVENUE_VERIFICATION
        assert flags[1].is_flagged
        assert cache.count() == 1  # 1 candidate cached
