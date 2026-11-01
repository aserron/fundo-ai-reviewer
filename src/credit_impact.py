"""Credit feature engineering, offer engine, window truncation detector, and Monte Carlo sensitivity simulator."""

from datetime import date
import json
from pathlib import Path
import random
import statistics
from typing import Any, Dict, List, Optional, Tuple, Union
from src.generate_data import BUSINESS_ARCHETYPES, generate_synthetic_dataset
from src.schemas import (
    BusinessMetrics,
    BusinessProfile,
    FundoGroup,
    LegacyLabel,
    PlaidTransaction,
    ReviewFlag,
)


def calculate_business_metrics(
    business: BusinessProfile,
    total_revenue: float,
    total_credits: float,
    total_debits: float,
    nsf_count: int,
    active_advance_debits: float,
    coverage_days: int = 90,
    state: str = "legacy",
) -> BusinessMetrics:
    """Compute financial features and the Fundo offer for a small business.

    Formula:
    offer = max(0, 1.2 * avg_monthly_revenue - 20 * other_funders_daily_payment)
    if nsf_count <= 5, else 0.0.
    """
    is_truncated = coverage_days < 85

    # Monthly revenue calculation (normalized by actual days span)
    if coverage_days == 90:
        avg_monthly_rev = total_revenue / 3.0
    else:
        avg_monthly_rev = total_revenue / (coverage_days / 30.0)

    # Revenue share of total deposits
    abs_credits = abs(total_credits)
    rev_share = (total_revenue / abs_credits) if abs_credits > 0 else 0.0

    # Active advance daily payment (65 active banking days in 90 calendar days)
    active_banking_days = (coverage_days / 90.0) * 65.0
    daily_mca_payment = (
        (active_advance_debits / active_banking_days) if active_banking_days > 0 else 0.0
    )

    # Offer calculation with hard NSF > 5 cliff
    if nsf_count > 5:
        calculated_offer = 0.0
    else:
        raw_offer = (1.2 * avg_monthly_rev) - (20.0 * daily_mca_payment)
        calculated_offer = max(0.0, raw_offer)

    return BusinessMetrics(
        business_id=business.business_id,
        state=state,
        total_credits=round(total_credits, 2),
        total_debits=round(total_debits, 2),
        avg_monthly_revenue=round(avg_monthly_rev, 2),
        revenue_share_of_deposits=round(rev_share, 4),
        nsf_overdraft_count=nsf_count,
        other_funders_daily_payment=round(daily_mca_payment, 2),
        calculated_offer=round(calculated_offer, 2),
        window_coverage_days=coverage_days,
        is_truncated_window=is_truncated,
    )


def check_window_coverage(coverage_days: int, total_revenue: float) -> Dict[str, Any]:
    """Detect truncated statement window (< 85 days) and calculate unnormalized bias."""
    is_truncated = coverage_days < 85
    naive_monthly = total_revenue / 3.0
    adjusted_monthly = total_revenue / (coverage_days / 30.0)
    distortion_pct = ((adjusted_monthly - naive_monthly) / adjusted_monthly) * 100.0

    return {
        "is_truncated": is_truncated,
        "naive_monthly_revenue": round(naive_monthly, 2),
        "adjusted_monthly_revenue": round(adjusted_monthly, 2),
        "distortion_pct": round(distortion_pct, 1),
    }


def evaluate_business_features(
    business: BusinessProfile,
    transactions: List[PlaidTransaction],
    labels: List[Union[LegacyLabel, ReviewFlag]],
    state: str,
) -> BusinessMetrics:
    """Aggregate transaction labels into business credit metrics."""
    total_credits = sum(tx.amount for tx in transactions if tx.is_credit)
    total_debits = sum(tx.amount for tx in transactions if tx.is_debit)
    
    total_revenue = 0.0
    nsf_count = 0
    active_advance_debits = 0.0

    for tx, item in zip(transactions, labels):
        # Extract group and revenue flag
        grp = getattr(item, "proposed_group", None) or getattr(item, "group", None)
        is_rev = item.is_revenue

        if is_rev:
            total_revenue += tx.abs_amount

        if grp in (FundoGroup.NSFS, FundoGroup.OVERDRAFT):
            nsf_count += 1

        if tx.is_debit and grp == FundoGroup.ACTIVE_ADVANCE:
            active_advance_debits += tx.abs_amount

    # Compute date span
    if transactions:
        min_date = date.fromisoformat(min(t.date for t in transactions))
        max_date = date.fromisoformat(max(t.date for t in transactions))
        coverage_days = (max_date - min_date).days + 1
    else:
        coverage_days = business.window_days

    return calculate_business_metrics(
        business=business,
        total_revenue=total_revenue,
        total_credits=total_credits,
        total_debits=total_debits,
        nsf_count=nsf_count,
        active_advance_debits=active_advance_debits,
        coverage_days=coverage_days,
        state=state,
    )


def run_sensitivity_simulation(
    trials_per_rate: int = 50,
    error_rates: Optional[List[float]] = None,
    seed: int = 42,
    transactions: Optional[List[PlaidTransaction]] = None,
    businesses: Optional[List[BusinessProfile]] = None,
) -> Dict[str, Any]:
    """Monte Carlo perturbation simulator across 2%, 5%, and 10% label error rates.

    Measures portfolio offer shifts, NSF rejection cliff triggers, and revenue errors.
    """
    if error_rates is None:
        error_rates = [0.02, 0.05, 0.10]

    rng = random.Random(seed)

    # Load or generate baseline dataset
    if transactions is None or businesses is None:
        biz_dicts, transactions = generate_synthetic_dataset(seed=seed)
        businesses = [BusinessProfile(**b) for b in biz_dicts]

    # Compute Ground Truth baseline metrics for each business
    tx_by_biz: Dict[str, List[PlaidTransaction]] = {b.business_id: [] for b in businesses}
    for tx in transactions:
        tx_by_biz[tx.business_id].append(tx)

    gt_metrics: Dict[str, BusinessMetrics] = {}
    for b in businesses:
        b_txs = tx_by_biz[b.business_id]
        gt_labels = [
            LegacyLabel(
                transaction_id=tx.transaction_id,
                group=tx.ground_truth.group if tx.ground_truth else FundoGroup.NONE,
                is_business=tx.ground_truth.is_business if tx.ground_truth else True,
                is_revenue=tx.ground_truth.is_revenue if tx.ground_truth else False,
            )
            for tx in b_txs
        ]
        gt_metrics[b.business_id] = evaluate_business_features(
            b, b_txs, gt_labels, state="ground_truth"
        )

    all_groups = list(FundoGroup)
    simulation_report: Dict[str, Any] = {}

    for rate in error_rates:
        portfolio_offer_errors: List[float] = []
        portfolio_revenue_errors: List[float] = []
        nsf_cliff_trigger_counts: List[int] = []

        for _ in range(trials_per_rate):
            trial_total_offer_error = 0.0
            trial_total_rev_error = 0.0
            trial_cliff_count = 0

            for b in businesses:
                b_txs = tx_by_biz[b.business_id]
                perturbed_labels: List[LegacyLabel] = []

                for tx in b_txs:
                    gt = tx.ground_truth
                    base_grp = gt.group if gt else FundoGroup.NONE
                    base_biz = gt.is_business if gt else True

                    if rng.random() < rate:
                        # Perturb label to a random different group
                        candidates = [g for g in all_groups if g != base_grp]
                        pert_grp = rng.choice(candidates)
                        pert_biz = base_biz if rng.random() > 0.1 else not base_biz
                        pert_rev = tx.is_credit and pert_biz and (pert_grp in (FundoGroup.REVENUE_VERIFICATION, FundoGroup.AUTO_DEPOSIT, FundoGroup.NONE))
                    else:
                        pert_grp = base_grp
                        pert_biz = base_biz
                        pert_rev = gt.is_revenue if gt else False

                    perturbed_labels.append(
                        LegacyLabel(
                            transaction_id=tx.transaction_id,
                            group=pert_grp,
                            is_business=pert_biz,
                            is_revenue=pert_rev,
                        )
                    )

                # Evaluate trial metrics
                trial_metric = evaluate_business_features(
                    b, b_txs, perturbed_labels, state="perturbed"
                )
                base_metric = gt_metrics[b.business_id]

                offer_diff = abs(trial_metric.calculated_offer - base_metric.calculated_offer)
                rev_diff = abs(trial_metric.avg_monthly_revenue - base_metric.avg_monthly_revenue)

                trial_total_offer_error += offer_diff
                trial_total_rev_error += rev_diff

                # Check if NSF cliff flipped decision
                if (base_metric.nsf_overdraft_count <= 5 and trial_metric.nsf_overdraft_count > 5) or (
                    base_metric.nsf_overdraft_count > 5 and trial_metric.nsf_overdraft_count <= 5
                ):
                    trial_cliff_count += 1

            portfolio_offer_errors.append(trial_total_offer_error)
            portfolio_revenue_errors.append(trial_total_rev_error)
            nsf_cliff_trigger_counts.append(trial_cliff_count)

        rate_key = f"{rate:.2f}"
        sorted_offers = sorted(portfolio_offer_errors)
        simulation_report[rate_key] = {
            "error_rate": rate,
            "trials": trials_per_rate,
            "mean_portfolio_offer_dollar_error": round(statistics.mean(portfolio_offer_errors), 2),
            "std_portfolio_offer_dollar_error": round(statistics.stdev(portfolio_offer_errors), 2) if len(portfolio_offer_errors) > 1 else 0.0,
            "p05_portfolio_offer_dollar_error": round(sorted_offers[int(0.05 * len(sorted_offers))], 2),
            "p95_portfolio_offer_dollar_error": round(sorted_offers[int(0.95 * len(sorted_offers))], 2),
            "mean_portfolio_monthly_rev_dollar_error": round(statistics.mean(portfolio_revenue_errors), 2),
            "mean_nsf_cliff_triggers_per_trial": round(statistics.mean(nsf_cliff_trigger_counts), 2),
        }

    return simulation_report
