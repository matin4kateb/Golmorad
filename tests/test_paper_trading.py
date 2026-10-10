from __future__ import annotations

import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from Database.SQLiteUtils import SQLiteUtils
from TradeUtils.PaperTrading import PaperTrader
from TradeUtils.WalkForwardBacktest import WalkForwardBacktest
from TradeUtils.RiskGate import RiskGate


class PaperTradingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = SQLiteUtils(Path(self.temp.name) / "paper.db")
        self.database.initialize()
        start = datetime.now(timezone.utc) - timedelta(hours=51)
        rows = []
        for i in range(51):
            price = 100 + i
            rows.append({"symbol": "TST-USD", "interval": "1h",
                         "timestamp": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                         "open": price, "high": price + 1, "low": price - 1,
                         "close": price, "volume": 0.01, "source": "unit-test"})
        self.database.upsert_candles(rows)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def report(self, *_args, **_kwargs):
        return {"symbol": "TST-USD", "interval": "1h", "signal": "buy",
                "reason": "mocked deterministic setup", "as_of": self.database.get_candles("TST-USD", "1h", 2)[0]["timestamp"],
                "source": "unit-test", "freshness": {"status": "fresh"},
                "trade_plan": {"side": "long", "entry_price": 149,
                               "stop_price": 140, "target_price": 167}}

    def test_fill_uses_next_candle_and_duplicate_is_rejected(self) -> None:
        with patch("TradeUtils.PaperTrading.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = self.report
            trader = PaperTrader(self.database)
            result = trader.run_once("TST-USD", "1h", 1000)
            self.assertEqual(result["status"], "partially_filled")
            self.assertGreater(result["latency_seconds"], 0)
            self.assertLessEqual(result["filled_quantity"], result["liquidity_limit"])
            repeated = trader.run_once("TST-USD", "1h", 1000)
            self.assertEqual(repeated["status"], "duplicate")
            self.assertEqual(len(trader.status()["orders"]), 1)

    def test_protective_target_exit_updates_pnl_and_position(self) -> None:
        with self.database.connection() as conn:
            conn.execute("UPDATE market_candles SET high=200 WHERE timestamp=(SELECT MAX(timestamp) FROM market_candles WHERE symbol='TST-USD')")
        with patch("TradeUtils.PaperTrading.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = self.report
            trader = PaperTrader(self.database)
            result = trader.run_once("TST-USD", "1h", 1000)
        self.assertIn("protective_exit", result)
        self.assertEqual(result["protective_exit"]["reason"], "target")
        state = trader.status()
        self.assertEqual(state["account"]["position_quantity"], 0)
        self.assertEqual(state["account"]["realized_pnl"], result["protective_exit"]["realized_pnl"])
        self.assertEqual(len(state["orders"]), 2)

    def test_emergency_stop_prevents_orders(self) -> None:
        trader = PaperTrader(self.database)
        trader.stop()
        result = trader.run_once("TST-USD", "1h", 1000)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(trader.status()["stopped"])

    def test_insufficient_history_does_not_create_account_trade(self) -> None:
        with self.database.connection() as conn:
            conn.execute("DELETE FROM market_candles WHERE timestamp=(SELECT MAX(timestamp) FROM market_candles WHERE symbol='TST-USD')")
        result = PaperTrader(self.database).run_once("TST-USD", "1h", 1000)
        self.assertEqual(result["status"], "no_trade")
        self.assertIn("51 completed candles", result["reason"])

    def test_current_forming_candle_is_not_used_as_a_fill_bar(self) -> None:
        with self.database.connection() as conn:
            conn.execute("UPDATE market_candles SET timestamp=? WHERE timestamp=(SELECT MAX(timestamp) FROM market_candles WHERE symbol='TST-USD')",
                         (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),))
        result = PaperTrader(self.database).run_once("TST-USD", "1h", 1000)
        self.assertEqual(result["status"], "no_trade")
        self.assertIn("completed candles", result["reason"])
        self.assertEqual(PaperTrader(self.database).status()["orders"], [])

    def test_empty_dataset_fails_closed_in_paper_and_backtest(self) -> None:
        with self.database.connection() as conn:
            conn.execute("DELETE FROM market_candles")
        trader = PaperTrader(self.database)
        paper = trader.run_once("TST-USD", "1h", 1000)
        backtest = WalkForwardBacktest(self.database).run("TST-USD", "1h", 1000)
        self.assertEqual(paper["status"], "no_trade")
        self.assertEqual(backtest["status"], "insufficient_data")
        self.assertEqual(trader.status()["orders"], [])

    def test_invalid_execution_candle_is_rejected(self) -> None:
        with self.database.connection() as conn:
            conn.execute("UPDATE market_candles SET open=-1 WHERE timestamp=(SELECT MAX(timestamp) FROM market_candles WHERE symbol='TST-USD')")
        with patch("TradeUtils.PaperTrading.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = self.report
            result = PaperTrader(self.database).run_once("TST-USD", "1h", 1000)
        self.assertEqual(result["status"], "no_trade")
        self.assertIn("positive-price", result["reason"])

    def test_stale_execution_data_is_rejected(self) -> None:
        with patch("TradeUtils.PaperTrading.StrategyAnalyzer") as analyzer, \
             patch("TradeUtils.PaperTrading.TechnicalAnalyzer._freshness", return_value={"status": "stale"}):
            analyzer.return_value.analyze.side_effect = self.report
            result = PaperTrader(self.database).run_once("TST-USD", "1h", 1000)
        self.assertEqual(result["status"], "no_trade")
        self.assertIn("stale", result["reason"])
        self.assertEqual(PaperTrader(self.database).run_once("TST-USD", "1h", 1000)["status"], "stopped")

    def test_sqlite_failure_is_not_silently_ignored(self) -> None:
        with patch.object(self.database, "connection", side_effect=sqlite3.OperationalError("database unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                PaperTrader(self.database)

    def test_risk_gate_fails_closed_for_missing_account_data(self) -> None:
        result = RiskGate().evaluate(initial_cash=1000, cash=1000, current_exposure=0,
            price=100, stop_price=90, quantity=0.1, fee_rate=0.001,
            daily_loss=0, drawdown=0, trades_today=0, data_fresh=False)
        self.assertFalse(result["allowed"])
        self.assertIn("fresh", result["reason"])

    def test_walk_forward_backtest_uses_only_prior_candles_and_records_costs(self) -> None:
        rows = []
        existing = self.database.get_candles("TST-USD", "1h", 100)
        last_time = datetime.fromisoformat(existing[-1]["timestamp"].replace("Z", "+00:00"))
        for i in range(10):
            price = 151 + i
            rows.append({"symbol": "TST-USD", "interval": "1h",
                         "timestamp": (last_time + timedelta(hours=i + 1)).isoformat().replace("+00:00", "Z"),
                         "open": price, "high": price + 1, "low": price - 1,
                         "close": price, "volume": 0.01, "source": "unit-test"})
        self.database.upsert_candles(rows)
        observed: list[tuple[str, str]] = []
        all_rows = self.database.get_candles("TST-USD", "1h", 100)

        def walk_forward_report(*_args, **kwargs):
            prefix = kwargs["candles"]
            observed.append((prefix[-1]["timestamp"], all_rows[len(prefix)]["timestamp"]))
            return {"signal": "buy", "as_of": prefix[-1]["timestamp"],
                    "freshness": {"status": "fresh"},
                    "trade_plan": {"stop_price": 140, "target_price": 180}}

        with patch("TradeUtils.WalkForwardBacktest.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = walk_forward_report
            report = WalkForwardBacktest(self.database).run("TST-USD", "1h", 1000, folds=5)
        self.assertEqual(report["status"], "complete")
        self.assertIn("fee_rate", report["cost_assumptions"])
        self.assertTrue(report["folds"])
        self.assertTrue(all(decision_time < fill_candle_time for decision_time, fill_candle_time in observed))

    def test_walk_forward_enforces_daily_trade_limit(self) -> None:
        with self.database.connection() as conn:
            conn.execute("DELETE FROM market_candles WHERE symbol='TST-USD'")
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        rows = []
        for i in range(57):
            timestamp = start + timedelta(minutes=i)
            rows.append({"symbol": "TST-USD", "interval": "1h",
                         "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                         "open": 100, "high": 102 if i >= 51 else 100,
                         "low": 99, "close": 100, "volume": 100,
                         "source": "unit-test"})
        self.database.upsert_candles(rows)
        observed: list[dict[str, object]] = []

        class CapturingGate:
            limits = RiskGate().limits

            def evaluate(self, **kwargs):
                observed.append(kwargs.copy())
                return RiskGate().evaluate(**kwargs)

        def buy_every_bar(*_args, **kwargs):
            prefix = kwargs["candles"]
            return {"signal": "buy", "as_of": prefix[-1]["timestamp"],
                    "freshness": {"status": "fresh"},
                    "trade_plan": {"stop_price": 90, "target_price": 101}}

        with patch("TradeUtils.WalkForwardBacktest.RiskGate", CapturingGate), \
             patch("TradeUtils.WalkForwardBacktest.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = buy_every_bar
            report = WalkForwardBacktest(self.database).run("TST-USD", "1h", 1000)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["trades"]), RiskGate().limits.max_trades_per_day)
        self.assertEqual([call["trades_today"] for call in observed[:6]], [0, 1, 2, 3, 4, 5])

    def test_walk_forward_does_not_reenter_at_an_exit_candles_past_open(self) -> None:
        with self.database.connection() as conn:
            conn.execute("DELETE FROM market_candles WHERE symbol='TST-USD'")
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        rows = []
        for i in range(53):
            timestamp = start + timedelta(minutes=i)
            rows.append({"symbol": "TST-USD", "interval": "1h",
                         "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                         "open": 100, "high": 100.5 if i == 51 else (102 if i == 52 else 100),
                         "low": 99, "close": 100, "volume": 100,
                         "source": "unit-test"})

        self.database.upsert_candles(rows)

        def buy_every_bar(*_args, **kwargs):
            prefix = kwargs["candles"]
            return {"signal": "buy", "as_of": prefix[-1]["timestamp"],
                    "freshness": {"status": "fresh"},
                    "trade_plan": {"stop_price": 90, "target_price": 101}}

        with patch("TradeUtils.WalkForwardBacktest.StrategyAnalyzer") as analyzer:
            analyzer.return_value.analyze.side_effect = buy_every_bar
            report = WalkForwardBacktest(self.database).run("TST-USD", "1h", 1000)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["trades"]), 1)
        self.assertEqual(report["trades"][0]["exit_at"], rows[52]["timestamp"])
        self.assertNotEqual(report["trades"][0]["fill_at"], rows[52]["timestamp"])


if __name__ == "__main__":
    unittest.main()
