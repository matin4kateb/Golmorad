from __future__ import annotations

import tempfile
import unittest
from threading import Event
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from Database.DataSeeder import DataSeeder
from Database.SQLiteUtils import SQLiteUtils, safe_error_detail
from TradeUtils.TechnicalAnalyzer import TechnicalAnalyzer


class RuntimeSafetyTests(unittest.TestCase):
    def test_collector_timeout_must_be_finite_and_positive(self) -> None:
        for value in ("invalid", "0", "-1", "inf", "nan"):
            with self.subTest(value=value), patch.dict("os.environ", {"DATA_HTTP_TIMEOUT_SECONDS": value}):
                with self.assertRaisesRegex(ValueError, "DATA_HTTP_TIMEOUT_SECONDS"):
                    DataSeeder(SQLiteUtils(Path(tempfile.gettempdir()) / "unused-golmorad.db"))

    def test_calendar_candles_close_on_calendar_boundaries(self) -> None:
        before_month = datetime(2026, 1, 31, 23, 59, tzinfo=timezone.utc)
        month_boundary = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(TechnicalAnalyzer._is_closed_candle(
            "2026-01-01T00:00:00Z", "1mo", before_month))
        self.assertTrue(TechnicalAnalyzer._is_closed_candle(
            "2026-01-01T00:00:00Z", "1mo", month_boundary))

    def test_provider_error_redacts_url_credentials_and_query(self) -> None:
        message = safe_error_detail(RuntimeError(
            "request failed https://user:password@feed.example/path?api_key=private-value&x=1"))
        self.assertNotIn("password", message)
        self.assertNotIn("private-value", message)
        self.assertIn("https://feed.example", message)

    def test_status_redacts_legacy_sync_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteUtils(Path(directory) / "runtime.db")
            database.initialize()
            with database.connection() as conn:
                conn.execute(
                    "INSERT INTO sync_runs(dataset,source,started_at,status,error) VALUES(?,?,?,?,?)",
                    ("news", "https://user:password@feed.example/rss?token=private-value",
                     "2026-10-10T00:00:00Z", "failed",
                     "request to https://feed.example/rss?api_key=private-value failed"),
                )
            row = database.get_sync_runs(1)[0]
            self.assertEqual(row["source"], "feed.example")
            self.assertNotIn("password", row["source"] + row["error"])
            self.assertNotIn("private-value", row["source"] + row["error"])

    def test_sync_history_retention_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteUtils(Path(directory) / "runtime.db")
            database.initialize()
            first = database.start_sync("test", "local")
            with database.connection() as conn:
                conn.executemany(
                    "INSERT INTO sync_runs(dataset,source,started_at,status) VALUES('test','local',?,'running')",
                    [(f"2026-01-01T00:{i % 60:02d}:{i % 60:02d}Z",) for i in range(1001)],
                )
            database.finish_sync(first, status="success")
            with database.connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
            self.assertEqual(count, 1000)

    def test_stop_waits_for_the_current_collection_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            seeder = DataSeeder(SQLiteUtils(Path(directory) / "runtime.db"))
            started, release = Event(), Event()

            def blocked_cycle() -> None:
                started.set()
                release.wait(2)

            seeder.run_once = blocked_cycle  # type: ignore[method-assign]
            seeder.start(60)
            self.assertTrue(started.wait(1))
            release.set()
            seeder.stop()
            self.assertFalse(seeder._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
