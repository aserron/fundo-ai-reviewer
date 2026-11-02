"""Unified CLI for Fundo AI Reviewer with offline caching, evaluation, and debrief tools."""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.cache_store import ReviewCacheService
from src.credit_impact import (
    evaluate_business_features,
    run_sensitivity_simulation,
)
from src.generate_data import BUSINESS_ARCHETYPES, generate_synthetic_dataset, save_dataset
from src.keyword_engine import LegacyKeywordEngine
from src.reviewer import (
    FakeLLMProvider,
    GeminiReviewerClient,
    OpenAIReviewerClient,
    ReviewOrchestrator,
)
from src.schemas import (
    BusinessProfile,
    FundoGroup,
    LegacyLabel,
    PlaidTransaction,
    ReviewFlag,
)

console = Console()


def load_dataset(data_dir: Path) -> tuple[List[BusinessProfile], List[PlaidTransaction]]:
    biz_path = data_dir / "businesses.json"
    tx_path = data_dir / "transactions.jsonl"

    if not biz_path.exists() or not tx_path.exists():
        console.print("[yellow]Data files not found. Auto-generating 10 business dataset...[/yellow]")
        save_dataset(output_dir=data_dir, seed=42)

    with open(biz_path, "r", encoding="utf-8") as f:
        biz_data = json.load(f)
    businesses = [BusinessProfile(**b) for b in biz_data]

    transactions: List[PlaidTransaction] = []
    with open(tx_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                transactions.append(PlaidTransaction.model_validate_json(line))

    return businesses, transactions


def cmd_run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    cache_dir = Path(getattr(args, "cache_dir", "cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_db_path = cache_dir / "llm_cache.db"
    cache_seed_path = cache_dir / "llm_cache_seed.jsonl"
    outputs_dir = Path(getattr(args, "output_dir", "outputs"))
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Initialize cache service with seed if available
    cache_service = ReviewCacheService(
        db_path=cache_db_path,
        seed_jsonl_path=cache_seed_path if cache_seed_path.exists() else None,
    )

    try:
        businesses, transactions = load_dataset(data_dir)
        console.print(
            Panel(
                f"[bold green]Starting Fundo AI Reviewer Pipeline[/bold green]\n"
                f"Businesses: {len(businesses)} | Total Transactions: {len(transactions):,} | "
                f"Cache Mode: {'[cyan]Offline/Committed Cache[/cyan]' if args.use_cache else '[magenta]Live/Hybrid[/magenta]'}",
                title="Pipeline Initialized",
            )
        )

        # Configure LLM Client
        gemini_key = os.environ.get("GEMINI_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not args.use_cache and gemini_key:
            console.print("[green][OK] GEMINI_API_KEY detected. Using Gemini 2.5 Flash reviewer.[/green]")
            provider = GeminiReviewerClient(model_id=args.model_id, api_key=gemini_key)
        elif not args.use_cache and openai_key:
            console.print("[green][OK] OPENAI_API_KEY detected. Using OpenAI GPT-4o-mini reviewer.[/green]")
            provider = OpenAIReviewerClient(
                model_id="gpt-4o-mini" if "gemini" in args.model_id else args.model_id,
                api_key=openai_key,
            )
        else:
            if not args.use_cache and not gemini_key and not openai_key:
                console.print(
                    "[yellow]! No GEMINI_API_KEY or OPENAI_API_KEY found. Falling back to committed cache & deterministic provider.[/yellow]"
                )
            provider = FakeLLMProvider()

        orchestrator = ReviewOrchestrator(
            cache_service=cache_service,
            llm_provider=provider,
            model_id=args.model_id,
            use_cache_only=args.use_cache,
        )

        keyword_engine = LegacyKeywordEngine()

        # Process transactions by business
        tx_by_biz: Dict[str, List[PlaidTransaction]] = {b.business_id: [] for b in businesses}
        for tx in transactions:
            tx_by_biz[tx.business_id].append(tx)

        all_review_flags: List[ReviewFlag] = []
        all_metrics: Dict[str, Dict[str, Any]] = {}

        summary_table = Table(
            title="Underwriting & Credit Offer Comparison (Legacy Baseline vs. Reviewer Corrected)",
            header_style="bold magenta",
        )
        summary_table.add_column("Business ID", style="cyan")
        summary_table.add_column("Industry", style="white")
        summary_table.add_column("Legacy Offer", justify="right")
        summary_table.add_column("Reviewed Offer", justify="right", style="green")
        summary_table.add_column("Ground Truth", justify="right", style="bold")
        summary_table.add_column("Legacy MAE", justify="right", style="red")
        summary_table.add_column("Reviewed MAE", justify="right", style="green")
        summary_table.add_column("Flags", justify="right")

        total_legacy_mae = 0.0
        total_reviewed_mae = 0.0

        for b in businesses:
            b_txs = tx_by_biz[b.business_id]

            # 1. Legacy keyword labeling
            legacy_labels = [keyword_engine.label_transaction(tx) for tx in b_txs]

            # 2. Hybrid LLM review
            flags = orchestrator.review_business(b, b_txs, legacy_labels)
            all_review_flags.extend(flags)

            # 3. Ground truth labels
            gt_labels = [
                LegacyLabel(
                    transaction_id=tx.transaction_id,
                    group=tx.ground_truth.group if tx.ground_truth else FundoGroup.NONE,
                    is_business=tx.ground_truth.is_business if tx.ground_truth else True,
                    is_revenue=tx.ground_truth.is_revenue if tx.ground_truth else False,
                )
                for tx in b_txs
            ]

            # Compute metrics across states
            legacy_m = evaluate_business_features(b, b_txs, legacy_labels, state="legacy")
            reviewed_m = evaluate_business_features(b, b_txs, flags, state="reviewed")
            gt_m = evaluate_business_features(b, b_txs, gt_labels, state="ground_truth")

            legacy_mae = abs(legacy_m.avg_monthly_revenue - gt_m.avg_monthly_revenue)
            reviewed_mae = abs(reviewed_m.avg_monthly_revenue - gt_m.avg_monthly_revenue)
            legacy_offer_err = abs(legacy_m.calculated_offer - gt_m.calculated_offer)
            reviewed_offer_err = abs(reviewed_m.calculated_offer - gt_m.calculated_offer)

            legacy_m.monthly_revenue_dollar_error = round(legacy_mae, 2)
            legacy_m.offer_dollar_error = round(legacy_offer_err, 2)
            reviewed_m.monthly_revenue_dollar_error = round(reviewed_mae, 2)
            reviewed_m.offer_dollar_error = round(reviewed_offer_err, 2)

            total_legacy_mae += legacy_mae
            total_reviewed_mae += reviewed_mae

            flagged_count = sum(1 for f in flags if f.is_flagged)

            summary_table.add_row(
                b.business_id,
                b.industry,
                f"${legacy_m.calculated_offer:,.2f}",
                f"${reviewed_m.calculated_offer:,.2f}",
                f"${gt_m.calculated_offer:,.2f}",
                f"${legacy_mae:,.2f}",
                f"${reviewed_mae:,.2f}",
                str(flagged_count),
            )

            all_metrics[b.business_id] = {
                "business_name": b.name,
                "industry": b.industry,
                "window_days": b.window_days,
                "bank_charges_nsf_fees": b.bank_charges_nsf_fees,
                "legacy": legacy_m.model_dump(),
                "reviewed": reviewed_m.model_dump(),
                "ground_truth": gt_m.model_dump(),
                "monthly_revenue_error_legacy": round(legacy_mae, 2),
                "monthly_revenue_error_reviewed": round(reviewed_mae, 2),
                "offer_error_legacy": round(legacy_offer_err, 2),
                "offer_error_reviewed": round(reviewed_offer_err, 2),
                "flagged_transactions_count": flagged_count,
            }

        console.print(summary_table)

        avg_legacy_mae = total_legacy_mae / len(businesses)
        avg_reviewed_mae = total_reviewed_mae / len(businesses)
        mae_reduction = (
            ((avg_legacy_mae - avg_reviewed_mae) / avg_legacy_mae) * 100.0
            if avg_legacy_mae > 0
            else 0.0
        )

        console.print(
            f"[bold]Portfolio Revenue MAE Improvement:[/bold] "
            f"[red]${avg_legacy_mae:,.2f}[/red] -> [green]${avg_reviewed_mae:,.2f}[/green] "
            f"([bold green]{mae_reduction:.1f}% reduction[/bold green])\n"
        )

        # Run Monte Carlo Sensitivity Simulation
        console.print("[cyan]Running Monte Carlo 2%, 5%, 10% Perturbation Simulation (50 trials each)...[/cyan]")
        sensitivity_report = run_sensitivity_simulation(
            trials_per_rate=50,
            error_rates=[0.02, 0.05, 0.10],
            seed=42,
            transactions=transactions,
            businesses=businesses,
        )

        # Save outputs
        flags_file = outputs_dir / "review_flags.jsonl"
        with open(flags_file, "w", encoding="utf-8") as f:
            for fl in all_review_flags:
                if fl.is_flagged:
                    f.write(fl.model_dump_json() + "\n")

        metrics_file = outputs_dir / "business_metrics.json"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(all_metrics, f, indent=2)

        sens_file = outputs_dir / "sensitivity_report.json"
        with open(sens_file, "w", encoding="utf-8") as f:
            json.dump(sensitivity_report, f, indent=2)

        # Export plain-text seed for git reproducibility
        cache_service.export_to_jsonl(cache_seed_path)

        telemetry = cache_service.get_telemetry_summary()
        console.print(
            Panel(
                f"Review Flags Written: [bold]{flags_file}[/bold] ({sum(1 for f in all_review_flags if f.is_flagged):,} doubts)\n"
                f"Business Metrics Written: [bold]{metrics_file}[/bold]\n"
                f"Sensitivity Report Written: [bold]{sens_file}[/bold]\n"
                f"SQLite WAL Cache: [bold]{cache_db_path}[/bold] ({telemetry['total_records']} cached)\n"
                f"Git-Synced Plaintext Seed: [bold]{cache_seed_path}[/bold]\n"
                f"Total LLM Spend: [bold green]${telemetry['total_cost_usd']:.4f} USD[/bold green] "
                f"(Tokens: {telemetry['total_prompt_tokens']:,} in / {telemetry['total_completion_tokens']:,} out)",
                title="Pipeline Outputs & Telemetry",
            )
        )
    finally:
        cache_service.close()


def cmd_review_file(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    if not input_path.exists():
        console.print(f"[bold red]Error: Input file {input_path} does not exist.[/bold red]")
        sys.exit(1)

    transactions: List[PlaidTransaction] = []
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if content.startswith("["):
            data = json.loads(content)
            for item in data:
                transactions.append(PlaidTransaction(**item))
        else:
            for line in content.splitlines():
                if line.strip():
                    transactions.append(PlaidTransaction.model_validate_json(line))

    biz = BusinessProfile(
        business_id="INTERVIEW_BIZ",
        name="Interviewer SMB Submission",
        industry=args.industry,
    )

    cache_service = ReviewCacheService(db_path=Path("cache/llm_cache.db"))
    try:
        gemini_key = os.environ.get("GEMINI_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        if gemini_key:
            provider = GeminiReviewerClient(api_key=gemini_key)
        elif openai_key:
            provider = OpenAIReviewerClient(api_key=openai_key)
        else:
            provider = FakeLLMProvider()

        orchestrator = ReviewOrchestrator(
            cache_service=cache_service,
            llm_provider=provider,
        )
        engine = LegacyKeywordEngine()

        legacy_labels = [engine.label_transaction(tx) for tx in transactions]
        flags = orchestrator.review_business(biz, transactions, legacy_labels)
        metrics = evaluate_business_features(biz, transactions, flags, state="reviewed")

        table = Table(title=f"Review Results for {biz.name} ({biz.industry})")
        table.add_column("Tx ID", style="dim")
        table.add_column("Counterparty", style="white")
        table.add_column("Amount", justify="right")
        table.add_column("Original Group", style="yellow")
        table.add_column("Reviewed Group", style="green")
        table.add_column("Rev?", justify="center")
        table.add_column("5-Sec Reason", style="cyan")

        for tx, fl in zip(transactions, flags):
            table.add_row(
                tx.transaction_id,
                tx.name[:30],
                f"${tx.amount:,.2f}",
                fl.original_group.value,
                fl.proposed_group.value,
                "YES" if fl.is_revenue else "NO",
                fl.reason,
            )

        console.print(table)
        console.print(
            Panel(
                f"Calculated Offer: [bold green]${metrics.calculated_offer:,.2f}[/bold green]\n"
                f"Monthly Revenue: ${metrics.avg_monthly_revenue:,.2f}\n"
                f"NSF Count: {metrics.nsf_overdraft_count} (Cliff threshold: 5)\n"
                f"Daily MCA Debits: ${metrics.other_funders_daily_payment:,.2f}/day",
                title="Underwriting Decision",
            )
        )
    finally:
        cache_service.close()


def cmd_cache_stats(args: argparse.Namespace) -> None:
    cache_dir = Path(getattr(args, "cache_dir", "cache"))
    cache_path = cache_dir / "llm_cache.db"
    if not cache_path.exists():
        console.print("[yellow]Cache database does not exist yet.[/yellow]")
        return

    cache = ReviewCacheService(db_path=cache_path)
    try:
        telemetry = cache.get_telemetry_summary()

        table = Table(title="ReviewCacheService Telemetry & LLM Accounting", header_style="bold green")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold white", justify="right")

        table.add_row("Total Cached Reviews", f"{telemetry['total_records']:,}")
        table.add_row("Total Prompt Tokens", f"{telemetry['total_prompt_tokens']:,}")
        table.add_row("Total Completion Tokens", f"{telemetry['total_completion_tokens']:,}")
        table.add_row("Estimated Spend (USD)", f"${telemetry['total_cost_usd']:.4f}")
        table.add_row("Average API Latency", f"{telemetry['avg_latency_ms']:.1f} ms")

        console.print(table)
    finally:
        cache.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fundo AI Reviewer: Deterministic + LLM Pipeline for Transaction Auditing"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 'run' subcommand
    run_parser = subparsers.add_parser("run", help="Run complete evaluation pipeline")
    run_parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Run 100% offline using committed SQLite cache and JSONL seed without API calls",
    )
    run_parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing businesses.json and transactions.jsonl",
    )
    run_parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory to write review_flags.jsonl, business_metrics.json, and sensitivity_report.json",
    )
    run_parser.add_argument(
        "--cache-dir",
        default="cache",
        help="Directory containing llm_cache.db and llm_cache_seed.jsonl",
    )
    run_parser.add_argument(
        "--model-id",
        default="gemini-2.5-flash",
        help="LLM model identifier for cache keying and API calls",
    )

    # 'review-file' subcommand
    rf_parser = subparsers.add_parser("review-file", help="Audit an external transaction JSON/JSONL file")
    rf_parser.add_argument("--input", required=True, help="Path to input JSON or JSONL transaction file")
    rf_parser.add_argument("--industry", default="General Retail", help="Business industry profile")

    # 'cache-stats' subcommand
    cs_parser = subparsers.add_parser("cache-stats", help="Display cache telemetry and spend statistics")
    cs_parser.add_argument("--cache-dir", default="cache", help="Directory containing llm_cache.db")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "review-file":
        cmd_review_file(args)
    elif args.command == "cache-stats":
        cmd_cache_stats(args)


if __name__ == "__main__":
    main()
