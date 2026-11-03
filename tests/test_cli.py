"""Tests for CLI entrypoints and offline execution."""

import argparse
from pathlib import Path
import pytest
from src.cli import cmd_cache_stats, cmd_run, load_env_file


def test_cli_run_offline_use_cache(tmp_path, monkeypatch):
    """Verify that cmd_run completes offline without API key using isolated output/cache dirs."""
    # Ensure no API key in environment
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    out_dir = tmp_path / "outputs"
    cache_dir = tmp_path / "cache"

    args = argparse.Namespace(
        command="run",
        use_cache=True,
        data_dir="data",
        output_dir=str(out_dir),
        cache_dir=str(cache_dir),
        model_id="gemini-2.5-flash",
    )
    # Should run cleanly and output files into isolated tmp_path
    cmd_run(args)

    assert (out_dir / "review_flags.jsonl").exists()
    assert (out_dir / "business_metrics.json").exists()
    assert (out_dir / "sensitivity_report.json").exists()
    assert (cache_dir / "llm_cache.db").exists()
    assert (cache_dir / "llm_cache_seed.jsonl").exists()


def test_cli_cache_stats():
    """Verify that cmd_cache_stats executes without error."""
    args = argparse.Namespace(command="cache-stats")
    cmd_cache_stats(args)


def test_load_env_file(tmp_path, monkeypatch):
    """Verify that load_env_file parses .env keys without overwriting existing env vars."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "existing-openai-key")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "GEMINI_API_KEY=\"test-gemini-123\"\n"
        "OPENAI_API_KEY=should-not-overwrite\n"
        "EMPTY_KEY=\n",
        encoding="utf-8",
    )

    load_env_file(env_file)
    import os

    assert os.environ.get("GEMINI_API_KEY") == "test-gemini-123"
    assert os.environ.get("OPENAI_API_KEY") == "existing-openai-key"
    assert "EMPTY_KEY" not in os.environ
