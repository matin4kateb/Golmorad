"""SQLite storage for market, news and sentiment knowledge."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "Database" / "golmorad.db"
SCHEMA_PATH = Path(__file__).with_name("golmorad.sql")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if value is not None else None


def safe_error_detail(error: str | Exception) -> str:
    """Strip URL credentials and query strings before persisting/displaying errors."""
    message = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)

    def origin_only(match: re.Match[str]) -> str:
        parsed = urlparse(match.group(0).rstrip(".,;:)]}"))
        return f"{parsed.scheme}://{parsed.hostname or 'redacted-host'}"

    message = re.sub(r"https?://[^\s\"'<>]+", origin_only, message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s*[:=]\s*\S+",
                     r"\1=<redacted>", message)
    return message[:2000]


class SQLiteUtils:
    """Small repository around SQLite with safe parameterized writes."""

    def __init__(self, database_path: str | Path | None = None):
        configured = database_path or os.getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH
        path = Path(configured).expanduser()
        self.database_path = path if path.is_absolute() else PROJECT_ROOT / path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connection() as conn:
            conn.executescript(schema)

    def upsert_candles(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [(
            row["symbol"], row["interval"], row["timestamp"], row["open"],
            row["high"], row["low"], row["close"], row.get("adjusted_close"),
            row.get("volume", 0), row.get("source", "yfinance"),
            row.get("fetched_at") or utc_now(),
        ) for row in rows]
        if not values:
            return 0
        with self.connection() as conn:
            conn.executemany("""INSERT INTO market_candles
                (symbol, interval, timestamp, open, high, low, close, adjusted_close,
                 volume, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, timestamp, source) DO UPDATE SET
                  open=excluded.open, high=excluded.high, low=excluded.low,
                  close=excluded.close, adjusted_close=excluded.adjusted_close,
                  volume=excluded.volume, fetched_at=excluded.fetched_at""", values)
        return len(values)

    def upsert_news(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [(
            row["source"], row.get("external_id"), row["url"], row["title"],
            row.get("summary"), row.get("published_at"), row.get("fetched_at") or utc_now(),
            row.get("language"), row.get("sentiment"), _json(row.get("raw")),
        ) for row in rows if row.get("url") and row.get("title")]
        if not values:
            return 0
        with self.connection() as conn:
            conn.executemany("""INSERT INTO news_articles
                (source, external_id, url, title, summary, published_at, fetched_at,
                 language, sentiment, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                  source=excluded.source, external_id=excluded.external_id,
                  title=excluded.title, summary=excluded.summary,
                  published_at=COALESCE(excluded.published_at, news_articles.published_at),
                  fetched_at=excluded.fetched_at, raw_json=excluded.raw_json""", values)
        return len(values)

    def upsert_sentiment(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [(
            row.get("provider", "alternative.me"), row.get("index_name", "crypto_fear_greed"),
            row["observed_at"], int(row["value"]), row["classification"],
            row.get("source_url"), row.get("fetched_at") or utc_now(), _json(row.get("raw")),
        ) for row in rows]
        if not values:
            return 0
        with self.connection() as conn:
            conn.executemany("""INSERT INTO sentiment_indices
                (provider, index_name, observed_at, value, classification, source_url,
                 fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, index_name, observed_at) DO UPDATE SET
                  value=excluded.value, classification=excluded.classification,
                  source_url=excluded.source_url, fetched_at=excluded.fetched_at,
                  raw_json=excluded.raw_json""", values)
        return len(values)

    def latest_timestamp(self, symbol: str, interval: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT MAX(timestamp) AS value FROM market_candles WHERE symbol=? AND interval=?",
                               (symbol, interval)).fetchone()
        return row["value"] if row else None

    def get_candles(self, symbol: str = "BTC-USD", interval: str = "1d",
                    limit: int = 500, *, start: str | None = None,
                    end: str | None = None) -> list[dict[str, Any]]:
        clauses, params = ["symbol=?", "interval=?"], [symbol, interval]
        if start:
            clauses.append("timestamp>=?"); params.append(start)
        if end:
            clauses.append("timestamp<?"); params.append(end)
        params.append(max(1, int(limit)))
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM market_candles WHERE {' AND '.join(clauses)} "
                                "ORDER BY timestamp DESC LIMIT ?", params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_news(self, limit: int = 100, *, since: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM news_articles"
        params: list[Any] = []
        if since:
            sql += " WHERE published_at>=?"; params.append(since)
        sql += " ORDER BY published_at DESC LIMIT ?"; params.append(max(1, int(limit)))
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["raw"] = json.loads(row.pop("raw_json")) if row.get("raw_json") else None
        return result

    def get_sentiment(self, index_name: str = "crypto_fear_greed", limit: int = 365) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM sentiment_indices WHERE index_name=? "
                                "ORDER BY observed_at DESC LIMIT ?", (index_name, max(1, int(limit)))).fetchall()
        return [dict(row) for row in reversed(rows)]

    def start_sync(self, dataset: str, source: str) -> int:
        with self.connection() as conn:
            cursor = conn.execute("INSERT INTO sync_runs(dataset, source, started_at, status) VALUES (?, ?, ?, 'running')",
                                  (dataset, source, utc_now()))
            return int(cursor.lastrowid)

    def finish_sync(self, run_id: int, *, status: str, rows_inserted: int = 0,
                    error: str | None = None) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE sync_runs SET finished_at=?, status=?, rows_inserted=?, error=? WHERE id=?",
                         (utc_now(), status, rows_inserted, error[:2000] if error else None, run_id))
            # Keep operational history useful but bounded for long-running services.
            conn.execute("DELETE FROM sync_runs WHERE id NOT IN "
                         "(SELECT id FROM sync_runs ORDER BY id DESC LIMIT 1000)")

    def get_sync_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent collection outcomes for the CLI status view."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            source = row.get("source")
            if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
                parsed = urlparse(source)
                row["source"] = parsed.hostname or "redacted-host"
            if row.get("error"):
                row["error"] = safe_error_detail(row["error"])
        return result
