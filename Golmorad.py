"""Golmorad knowledge-base updater entry point."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import threading
from pathlib import Path


def _load_local_env() -> None:
    """Load simple KEY=VALUE settings without overwriting the process environment."""
    env_file = Path(__file__).resolve().with_name(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    _load_local_env()
    log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    if log_level not in logging.getLevelNamesMapping():
        raise SystemExit("APP_LOG_LEVEL must be a standard Python logging level (DEBUG, INFO, WARNING, ERROR or CRITICAL).")
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Analyze crypto market data or update Golmorad's knowledge base")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--once", action="store_true", help="run one data update cycle and exit")
    action.add_argument("--analyze", action="store_true", help="print a rule-based spot-market analysis as JSON")
    action.add_argument("--status", action="store_true", help="show recent data collection outcomes as JSON")
    action.add_argument("--gui", action="store_true", help="open the local desktop dashboard")
    action.add_argument("--paper", action="store_true", help="process one look-ahead-safe paper decision; requires --capital")
    action.add_argument("--paper-status", action="store_true", help="show paper account, orders, events and stop state as JSON")
    action.add_argument("--stop", action="store_true", help="activate the persistent paper trading emergency stop")
    action.add_argument("--resume-paper", action="store_true", help="resume paper trading after an explicit stop")
    action.add_argument("--live", action="store_true", help="live trading is currently disabled pending verified exchange contracts")
    action.add_argument("--backtest", action="store_true", help="run the separate expanding walk-forward backtest on saved candles")
    action.add_argument("--optimize", action="store_true", help="select bounded strategy parameters on chronological validation data and report an untouched holdout")
    parser.add_argument("--auto-optimize", action="store_true", help="select parameters from prior candles before a paper decision; insufficient validation data fails closed")
    parser.add_argument("--interval", type=int,
                        default=os.getenv("DATA_REFRESH_INTERVAL_SECONDS", "900"),
                        help="seconds between background update cycles (minimum 60)")
    parser.add_argument("--symbol", default=os.getenv("MARKET_SYMBOL", "BTC-USD"),
                        help="market symbol (default: MARKET_SYMBOL or BTC-USD)")
    parser.add_argument("--candle-interval", default=os.getenv("MARKET_INTERVAL", "1d"),
                        help="candle interval used for analysis (default: MARKET_INTERVAL or 1d)")
    parser.add_argument("--capital", type=float, help="account balance in the quote currency; enables position sizing")
    parser.add_argument("--risk-percent", type=float,
                        help="account percentage to risk on an analysis position-size estimate (default: 1)")
    args = parser.parse_args()

    if args.interval < 60 and not (args.once or args.analyze or args.status):
        parser.error("--interval must be at least 60 seconds")
    if args.capital is not None and not (args.analyze or args.paper or args.backtest or args.optimize):
        parser.error("--capital can only be used with --analyze, --paper, --backtest or --optimize")
    if args.capital is not None and (not math.isfinite(args.capital) or args.capital <= 0):
        parser.error("--capital must be a finite number greater than zero")
    if args.risk_percent is not None and not args.analyze:
        parser.error("--risk-percent can only be used with --analyze")
    if args.risk_percent is not None:
        if not math.isfinite(args.risk_percent) or not 0 < args.risk_percent <= 100:
            parser.error("--risk-percent must be finite, greater than 0 and at most 100")
        if args.capital is None:
            parser.error("--risk-percent requires --capital")
    if (args.paper or args.backtest) and args.capital is None:
        parser.error("--paper and --backtest require explicit --capital")
    if args.optimize and args.capital is None:
        parser.error("--optimize requires explicit --capital")
    if args.auto_optimize and not args.paper:
        parser.error("--auto-optimize can only be used with --paper")
    if (args.analyze or args.paper or args.backtest or args.optimize) and (
            not args.symbol.strip() or not args.candle_interval.strip()):
        parser.error("--symbol and --candle-interval must not be empty")

    from Database.DataSeeder import DataSeeder
    from Database.SQLiteUtils import SQLiteUtils

    if args.live:
        parser.error("Live trading is disabled: Bit24 balance and order-reconciliation contracts have not been verified for safe unattended execution.")

    if args.backtest:
        from TradeUtils.WalkForwardBacktest import WalkForwardBacktest

        database = SQLiteUtils()
        database.initialize()
        result = WalkForwardBacktest(database).run(args.symbol, args.candle_interval, args.capital)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.optimize:
        from TradeUtils.ParameterOptimizer import ParameterOptimizer

        database = SQLiteUtils()
        database.initialize()
        result = ParameterOptimizer(database).run(args.symbol, args.candle_interval, args.capital)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.gui:
        from GUI.app import launch

        launch()
        return 0

    if args.paper or args.paper_status or args.stop or args.resume_paper:
        from TradeUtils.PaperTrading import PaperTrader

        trader = PaperTrader(SQLiteUtils())
        if args.stop:
            trader.stop("operator_stop_cli")
            print(json.dumps({"mode": "paper", "stopped": True}, ensure_ascii=False))
        elif args.resume_paper:
            trader.resume()
            print(json.dumps({"mode": "paper", "stopped": False}, ensure_ascii=False))
        elif args.paper_status:
            print(json.dumps(trader.status(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(trader.run_once(args.symbol, args.candle_interval, args.capital,
                                             auto_optimize=args.auto_optimize),
                             ensure_ascii=False, indent=2))
        return 0

    if args.analyze or args.status:
        database = SQLiteUtils()
        database.initialize()
        if args.status:
            print(json.dumps({"recent_sync_runs": database.get_sync_runs()}, ensure_ascii=False, indent=2))
            return 0

        from TradeUtils.FundamentalAnalyzer import FundamentalAnalyzer
        from TradeUtils.RiskManager import RiskManager
        from TradeUtils.StrategyAnalyzer import StrategyAnalyzer

        report = StrategyAnalyzer().analyze(args.symbol, args.candle_interval)
        report["fundamental"] = FundamentalAnalyzer(database).analyze()
        if args.capital is not None:
            if report.get("signal") == "buy" and report.get("trade_plan"):
                report["position_size"] = RiskManager.calculate_position_size(
                    args.capital, report["trade_plan"]["entry_price"],
                    report["trade_plan"]["stop_price"], args.risk_percent or 1.0,
                )
            else:
                report["position_size"] = None
                report["position_size_note"] = "No long setup; no position size was calculated."
        report["model_type"] = "explainable_ruleset_not_machine_learning"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    seeder = DataSeeder()
    if args.once:
        print(seeder.run_once())
        return 0

    stopped = threading.Event()

    def stop_handler(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_handler)
    seeder.start(args.interval)
    logging.getLogger(__name__).info("Knowledge-base refresh started; interval=%ds", args.interval)
    stopped.wait()
    seeder.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
