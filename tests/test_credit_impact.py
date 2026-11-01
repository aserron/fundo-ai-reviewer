"""Tests for Credit Impact, Offer Engine, NSF > 5 cliff, and Monte Carlo simulator."""

import pytest
from src.schemas import BusinessMetrics, BusinessProfile, FundoGroup, PlaidTransaction, ReviewFlag
from src.credit_impact import (
    calculate_business_metrics,
    check_window_coverage,
    run_sensitivity_simulation,
)


@pytest.fixture
def sample_business():
    return BusinessProfile(
        business_id="BIZ_TEST",
        name="Test Corp",
        industry="Retail",
        bank_charges_nsf_fees=True,
        window_days=90,
    )


class TestOfferEngineAndInvariants:
    def test_offer_calculation_nsf_5_vs_6_cliff(self, sample_business):
        """Offer is calculated normally at NSF=5, but drops strictly to 0 at NSF=6."""
        # Baseline: $30,000 monthly revenue ($90,000 total revenue), $100/day active advance
        # Base offer = 1.2 * 30,000 - 20 * 100 = 36,000 - 2,000 = 34,000
        
        # Test NSF = 5
        metrics_5 = calculate_business_metrics(
            business=sample_business,
            total_revenue=90000.0,
            total_credits=-100000.0,
            total_debits=60000.0,
            nsf_count=5,
            active_advance_debits=6500.0,  # 6500 / 65 days = $100/day
            coverage_days=90,
            state="test",
        )
        assert metrics_5.nsf_overdraft_count == 5
        assert metrics_5.other_funders_daily_payment == 100.0
        assert metrics_5.avg_monthly_revenue == 30000.0
        assert metrics_5.calculated_offer == 34000.0

        # Test NSF = 6 (Cliff Triggered)
        metrics_6 = calculate_business_metrics(
            business=sample_business,
            total_revenue=90000.0,
            total_credits=-100000.0,
            total_debits=60000.0,
            nsf_count=6,
            active_advance_debits=6500.0,
            coverage_days=90,
            state="test",
        )
        assert metrics_6.nsf_overdraft_count == 6
        assert metrics_6.calculated_offer == 0.0  # Rejected by policy!

    def test_window_truncation_detection(self):
        """Detect truncated statement window (61 days < 85 days) and demonstrate unnormalized bias."""
        # 61 days with $61,000 revenue
        res = check_window_coverage(
            coverage_days=61,
            total_revenue=61000.0,
        )
        assert res["is_truncated"]
        # Naive 90-day assumption: 61,000 / 3 = 20,333.33
        assert pytest.approx(res["naive_monthly_revenue"], 0.01) == 20333.33
        # Coverage-adjusted assumption: 61,000 / (61 / 30) = 30,000.00
        assert pytest.approx(res["adjusted_monthly_revenue"], 0.01) == 30000.00
        # Percentage distortion is ~32.2%
        assert pytest.approx(res["distortion_pct"], 0.1) == 32.2

    def test_asymmetric_error_cost(self, sample_business):
        """Active advance error of $1000 moves offer by 20x daily payment;

        Revenue error of $1000 moves offer by 1.2 * (1000 / 3) = $400.
        """
        # Baseline offer: 1.2 * 30000 - 20 * 100 = 34,000
        # Add $1,000 to Active advance: daily pmt increases by 1000 / 65 = 15.3846
        # Offer decreases by 20 * 15.3846 = 307.69
        m_adv = calculate_business_metrics(
            business=sample_business,
            total_revenue=90000.0,
            total_credits=-100000.0,
            total_debits=60000.0,
            nsf_count=2,
            active_advance_debits=7500.0,  # +$1000
            coverage_days=90,
            state="test",
        )
        delta_adv = 34000.0 - m_adv.calculated_offer
        assert pytest.approx(delta_adv, 0.1) == 307.69

        # Add $1,000 to total 90-day revenue: monthly rev increases by 1000 / 3 = 333.33
        # Offer increases by 1.2 * 333.33 = 400.00
        m_rev = calculate_business_metrics(
            business=sample_business,
            total_revenue=91000.0,  # +$1000
            total_credits=-100000.0,
            total_debits=60000.0,
            nsf_count=2,
            active_advance_debits=6500.0,
            coverage_days=90,
            state="test",
        )
        delta_rev = m_rev.calculated_offer - 34000.0
        assert pytest.approx(delta_rev, 0.1) == 400.00


class TestSensitivitySimulator:
    def test_simulation_runs_across_rates(self):
        """Monte Carlo simulation executes across 2%, 5%, and 10% rates."""
        results = run_sensitivity_simulation(
            trials_per_rate=10,
            error_rates=[0.02, 0.05, 0.10],
            seed=42,
        )
        assert "0.02" in results
        assert "0.05" in results
        assert "0.10" in results
        assert results["0.02"]["mean_portfolio_offer_dollar_error"] >= 0.0
        assert results["0.10"]["mean_portfolio_offer_dollar_error"] >= results["0.02"]["mean_portfolio_offer_dollar_error"]
