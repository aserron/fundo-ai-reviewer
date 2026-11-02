"""ReviewCacheService: Embedded SQLite WAL persistence, SHA-256 keys, and cost telemetry."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def normalize_string(text: str) -> str:
    """Normalize whitespace and lowercase for cache key determinism."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def normalize_counterparty_for_key(text: str) -> str:
    """Normalize counterparty text and mask variable 3+ digit sequence/batch IDs (except 941 tax code)."""
    cleaned = normalize_string(text)
    return re.sub(r"\b(?!941\b)\d{3,}\b", "#", cleaned)


def compute_amount_bucket(amount: float) -> Tuple[str, str]:
    """Return (amount_sign, amount_bucket) for canonical cache keying."""
    if amount < 0:
        sign = "credit"
    elif amount > 0:
        sign = "debit"
    else:
        return "zero", "0"

    mag = abs(amount)
    if mag < 50.0:
        bucket = "<50"
    elif mag < 250.0:
        bucket = "50-250"
    elif mag < 1000.0:
        bucket = "250-1000"
    elif mag < 5000.0:
        bucket = "1000-5000"
    elif mag < 15000.0:
        bucket = "5000-15000"
    else:
        bucket = "15000+"
    return sign, bucket


def make_cache_key(
    counterparty: str,
    amount: float,
    industry: str,
    legacy_group: Any,
    model_id: str = "gemini-2.5-flash",
    prompt_version: str = "v1.0",
) -> str:
    """Compute a canonical SHA-256 cache key.

    Invariance rules:
    - Counterparty name is case-insensitive, collapsed of excess whitespace, and normalized for batch IDs.
    - Amount is represented by sign ('credit'/'debit'/'zero') and magnitude bucket.
    - Industry and legacy group are normalized.
    - Model ID and prompt version are strictly included to prevent cross-model/prompt leakage.
    """
    clean_counterparty = normalize_counterparty_for_key(counterparty)
    clean_industry = normalize_string(industry)
    group_str = legacy_group.value if hasattr(legacy_group, "value") else str(legacy_group)
    clean_group = normalize_string(group_str)
    clean_model = model_id.strip().lower()
    clean_prompt = prompt_version.strip()

    amount_sign, amount_bucket = compute_amount_bucket(amount)

    canonical_signature = (
        f"{clean_counterparty}|{amount_sign}|{amount_bucket}|{clean_industry}|"
        f"{clean_group}|{clean_model}|{clean_prompt}"
    )
    return hashlib.sha256(canonical_signature.encode("utf-8")).hexdigest()


class ReviewCacheService:
    """Embedded SQLite caching and telemetry service for LLM reviews."""

    def __init__(
        self,
        db_path: str | Path = Path("cache/llm_cache.db"),
        seed_jsonl_path: Optional[str | Path] = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

        if seed_jsonl_path:
            seed_path = Path(seed_jsonl_path)
            if seed_path.exists():
                self.seed_from_jsonl(seed_path)

    def _init_db(self) -> None:
        """Initialize table schema and WAL performance pragmas."""
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_review_cache (
                    cache_key TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    tx_signature TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                );
                """
            )

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single cache entry by its key."""
        cursor = self.conn.execute(
            "SELECT * FROM llm_review_cache WHERE cache_key = ?", (cache_key,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_many(self, cache_keys: List[str]) -> Dict[str, Dict[str, Any]]:
        """Retrieve multiple entries by key list."""
        if not cache_keys:
            return {}
        
        results: Dict[str, Dict[str, Any]] = {}
        # Chunk queries to respect SQLite variable limits
        chunk_size = 500
        for i in range(0, len(cache_keys), chunk_size):
            chunk = cache_keys[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"SELECT * FROM llm_review_cache WHERE cache_key IN ({placeholders})",
                chunk,
            )
            for row in cursor.fetchall():
                d = dict(row)
                results[d["cache_key"]] = d
        return results

    def put_many(self, entries: List[Dict[str, Any]]) -> None:
        """Insert or replace cache entries."""
        if not entries:
            return
        
        records = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for e in entries:
            records.append(
                (
                    e["cache_key"],
                    e.get("model_id", "gemini-2.5-flash"),
                    e.get("prompt_version", "v1.0"),
                    e.get("tx_signature", ""),
                    e["decision_json"],
                    e.get("prompt_tokens", 0),
                    e.get("completion_tokens", 0),
                    e.get("cost_usd", 0.0),
                    e.get("latency_ms", 0.0),
                    e.get("created_at", now_iso),
                )
            )

        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO llm_review_cache (
                    cache_key, model_id, prompt_version, tx_signature,
                    decision_json, prompt_tokens, completion_tokens,
                    cost_usd, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

    def partition_batch(
        self, items: List[T], key_fn: Callable[[T], str]
    ) -> Tuple[Dict[str, Dict[str, Any]], List[T]]:
        """Partition candidate items into cached hits and missing items.

        Returns:
            (cached_hits, missing_items)
        """
        if not items:
            return {}, []

        keys = [key_fn(item) for item in items]
        cached_hits = self.get_many(keys)
        missing_items = [item for item in items if key_fn(item) not in cached_hits]
        return cached_hits, missing_items

    def count(self) -> int:
        """Return total cached review records."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM llm_review_cache")
        return cursor.fetchone()[0]

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Aggregate token usage, spend, and latency."""
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(prompt_tokens), 0),
                COALESCE(SUM(completion_tokens), 0),
                COALESCE(SUM(cost_usd), 0.0),
                COALESCE(AVG(latency_ms), 0.0)
            FROM llm_review_cache
            """
        )
        row = cursor.fetchone()
        return {
            "total_records": row[0],
            "total_prompt_tokens": row[1],
            "total_completion_tokens": row[2],
            "total_cost_usd": round(row[3], 6),
            "avg_latency_ms": round(row[4], 2),
        }

    def export_to_jsonl(self, export_path: str | Path) -> int:
        """Export all cache records to a plain-text JSONL mirror."""
        path = Path(export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cursor = self.conn.execute(
            "SELECT * FROM llm_review_cache ORDER BY cache_key ASC"
        )
        count = 0
        with open(path, "w", encoding="utf-8") as f:
            for row in cursor.fetchall():
                f.write(json.dumps(dict(row)) + "\n")
                count += 1
        return count

    def seed_from_jsonl(self, seed_path: str | Path) -> int:
        """Hydrate SQLite cache from a committed JSONL seed file."""
        path = Path(seed_path)
        if not path.exists():
            return 0
        
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        
        self.put_many(entries)
        return len(entries)

    def close(self) -> None:
        self.conn.close()
