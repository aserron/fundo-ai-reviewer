"""Tests for SQLite WAL ReviewCacheService, canonical hash determinism, and telemetry."""

import json
from pathlib import Path
import pytest
from src.schemas import FundoGroup, PlaidTransaction, ReviewerProposal
from src.cache_store import ReviewCacheService, make_cache_key


@pytest.fixture
def golden_cache_path() -> Path:
    return Path(__file__).parent / "fixtures" / "golden_cache.jsonl"


class TestCanonicalHashDeterminism:
    def test_whitespace_and_case_invariance(self):
        k1 = make_cache_key(
            counterparty="ACH CREDIT  SQ *JOES   TACOS ",
            amount=-1250.50,
            industry="Restaurant",
            legacy_group=FundoGroup.REVENUE_VERIFICATION,
            model_id="gemini-2.5-flash",
            prompt_version="v1.0",
        )
        k2 = make_cache_key(
            counterparty="ach credit sq *joes tacos",
            amount=-1250.50,
            industry="restaurant",
            legacy_group="Revenue verification",
            model_id="gemini-2.5-flash",
            prompt_version="v1.0",
        )
        assert k1 == k2
        assert len(k1) == 64  # SHA-256 hex length

    def test_key_changes_with_parameters(self):
        base_kwargs = dict(
            counterparty="SQ *JOES TACOS",
            amount=-1250.50,
            industry="Restaurant",
            legacy_group=FundoGroup.REVENUE_VERIFICATION,
            model_id="gemini-2.5-flash",
            prompt_version="v1.0",
        )
        base_key = make_cache_key(**base_kwargs)

        # Change counterparty
        assert make_cache_key(**{**base_kwargs, "counterparty": "TOAST SETTLEMENT"}) != base_key
        # Change amount sign / bucket
        assert make_cache_key(**{**base_kwargs, "amount": 1250.50}) != base_key
        # Change industry
        assert make_cache_key(**{**base_kwargs, "industry": "Wholesale"}) != base_key
        # Change legacy group
        assert make_cache_key(**{**base_kwargs, "legacy_group": FundoGroup.NONE}) != base_key
        # Change model
        assert make_cache_key(**{**base_kwargs, "model_id": "gpt-4o-mini"}) != base_key
        # Change prompt version
        assert make_cache_key(**{**base_kwargs, "prompt_version": "v1.1"}) != base_key


class TestReviewCacheService:
    def test_hydration_from_golden_cache(self, tmp_path, golden_cache_path):
        db_path = tmp_path / "test_cache.db"
        cache = ReviewCacheService(db_path=db_path, seed_jsonl_path=golden_cache_path)
        
        # Verify 10 records loaded
        assert cache.count() == 10

        # Retrieve a record
        hit = cache.get("k01_sq_joes")
        assert hit is not None
        assert hit["model_id"] == "gemini-2.5-flash"
        decision = json.loads(hit["decision_json"])
        assert decision["group"] == "Revenue verification"
        assert decision["confidence"] == 0.95

    def test_partition_batch(self, tmp_path, golden_cache_path):
        db_path = tmp_path / "test_cache.db"
        cache = ReviewCacheService(db_path=db_path, seed_jsonl_path=golden_cache_path)

        # Prepare 12 items: 2 hits (from golden) and 2 new items
        items = [
            {"key": "k01_sq_joes", "tx_id": "tx_1"},
            {"key": "k02_sq_loan", "tx_id": "tx_2"},
            {"key": "unseen_key_999", "tx_id": "tx_3"},
            {"key": "unseen_key_888", "tx_id": "tx_4"},
        ]

        cached_hits, missing = cache.partition_batch(items, key_fn=lambda x: x["key"])
        assert len(cached_hits) == 2
        assert set(cached_hits.keys()) == {"k01_sq_joes", "k02_sq_loan"}
        assert len(missing) == 2
        assert [m["tx_id"] for m in missing] == ["tx_3", "tx_4"]

    def test_telemetry_aggregation(self, tmp_path, golden_cache_path):
        db_path = tmp_path / "test_cache.db"
        cache = ReviewCacheService(db_path=db_path, seed_jsonl_path=golden_cache_path)

        telemetry = cache.get_telemetry_summary()
        assert telemetry["total_records"] == 10
        assert telemetry["total_prompt_tokens"] == 1200
        assert telemetry["total_completion_tokens"] > 0
        assert telemetry["total_cost_usd"] > 0.0
        assert telemetry["avg_latency_ms"] > 0.0

    def test_jsonl_roundtrip_export_and_seed(self, tmp_path):
        db_path_1 = tmp_path / "db1.db"
        export_file = tmp_path / "exported.jsonl"
        db_path_2 = tmp_path / "db2.db"

        cache1 = ReviewCacheService(db_path=db_path_1)
        cache1.put_many([
            {
                "cache_key": "custom_k1",
                "model_id": "gemini-2.5-flash",
                "prompt_version": "v1.0",
                "tx_signature": "custom|100",
                "decision_json": json.dumps({"group": "NSFs", "confidence": 0.99}),
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "cost_usd": 0.00003,
                "latency_ms": 110.0,
                "created_at": "2026-01-01T00:00:00",
            }
        ])
        assert cache1.count() == 1
        cache1.export_to_jsonl(export_file)
        assert export_file.exists()

        cache2 = ReviewCacheService(db_path=db_path_2, seed_jsonl_path=export_file)
        assert cache2.count() == 1
        record = cache2.get("custom_k1")
        assert record is not None
        assert record["model_id"] == "gemini-2.5-flash"
