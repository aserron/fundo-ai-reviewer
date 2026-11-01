"""Tests for CLI entrypoints and offline execution."""

import argparse
from pathlib import Path
import pytest
from src.cli import cmd_cache_stats, cmd_run


def test_cli_run_offline_use_cache(tmp_path, monkeypatch):
    """Verify that cmd_run completes offline without API key."""
    # Ensure no API key in environment
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    args = argparse.Namespace(
        command="run",
        use_cache=True,
        data_dir="data",
        model_id="gemini-2.5-flash",
    )
    # Should run cleanly and output files
    cmd_run(args)

    assert Path("outputs/review_flags.jsonl").exists()
    assert Path("outputs/business_metrics.json").exists()
    assert Path("outputs/sensitivity_report.json").exists()
    assert Path("cache/llm_cache.db").exists()
    assert Path("cache/llm_cache_seed.jsonl").exists()


def test_cli_cache_stats():
    """Verify that cmd_cache_stats executes without error."""
    args = argparse.Namespace(command="cache-stats")
    cmd_cache_stats(args)
