"""Constrained strategy-parameter selection with a chronological holdout.

The selector only searches unleveraged spot signal/exit parameters. It reports
the untouched final segment separately and never changes the fixed RiskGate.
"""

from __future__ import annotations

import math
from typing import Any

from Database.SQLiteUtils import SQLiteUtils
from TradeUtils.TechnicalAnalyzer import TechnicalAnalyzer
from TradeUtils.WalkForwardBacktest import WalkForwardBacktest


BASELINE = {"stop_atr_multiple": 2.0, "reward_risk_ratio": 2.0,
            "rsi_lower": 30.0, "rsi_upper": 70.0}


class ParameterOptimizer:
    """Select a small parameter grid on validation data, then report holdout."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()
        self.backtester = WalkForwardBacktest(self.database)

    def run(self, symbol: str, interval: str, initial_cash: float, *,
            candles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if isinstance(initial_cash, bool) or not isinstance(initial_cash, (int, float)) or not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Optimization capital must be a finite positive amount")
        candles = candles if candles is not None else self.database.get_candles(symbol, interval, limit=2000)
        invalid = TechnicalAnalyzer._validate_candles(candles)
        if invalid:
            return {"status": "invalid_data", "reason": invalid}

        count = len(candles)
        validation_start = max(51, int(count * 0.60))
        holdout_start = max(validation_start + 1, int(count * 0.80))
        if (count - holdout_start < 10 or validation_start - 51 < 10
                or holdout_start - validation_start < 10):
            return {"status": "insufficient_data", "observations": count,
                    "reason": "Usable chronological training, validation, and untouched holdout segments are required (at least 10 candles per segment after indicator warm-up)."}

        candidates: list[dict[str, float]] = []
        # Nine bounded combinations keep the repeated walk-forward evaluation
        # practical. Stop width and RSI tolerance move together; this is a
        # deliberately small search, not a claim of globally optimal settings.
        for stop_atr, rsi_upper in ((1.5, 65.0), (2.0, 70.0), (2.5, 75.0)):
            for reward_risk in (1.5, 2.0, 2.5):
                candidates.append({"stop_atr_multiple": stop_atr,
                    "reward_risk_ratio": reward_risk, "rsi_lower": 30.0,
                    "rsi_upper": rsi_upper})

        rows: list[dict[str, Any]] = []
        for parameters in candidates:
            result = self.backtester.run(symbol, interval, float(initial_cash), folds=2,
                parameters=parameters, candles=candles[:holdout_start],
                test_start_index=validation_start)
            closed = [trade for trade in result.get("trades", []) if "pnl" in trade]
            trades = len(closed)
            drawdown = float(result.get("max_drawdown_percent", 0.0))
            returned = float(result.get("return_percent", 0.0))
            eligible = result.get("status") == "complete" and trades >= 3
            score = returned - 0.5 * drawdown if eligible else None
            rows.append({"parameters": parameters, "validation_status": result.get("status"),
                         "closed_trades": trades, "return_percent": returned,
                         "max_drawdown_percent": drawdown, "selection_score": score})

        eligible_rows = [row for row in rows if row["selection_score"] is not None]
        selected = max(eligible_rows, key=lambda row: (row["selection_score"],
            -abs(row["parameters"]["stop_atr_multiple"] - 2.0),
            -abs(row["parameters"]["reward_risk_ratio"] - 2.0),
            -abs(row["parameters"]["rsi_upper"] - 70.0))) if eligible_rows else None
        parameters = selected["parameters"] if selected else BASELINE
        holdout = self.backtester.run(symbol, interval, float(initial_cash), folds=2,
            parameters=parameters, candles=candles, test_start_index=holdout_start)
        return {"status": "complete", "symbol": symbol, "interval": interval,
                "observations": count,
                "windows": {"validation_start": candles[validation_start]["timestamp"],
                            "validation_end": candles[holdout_start - 1]["timestamp"],
                            "holdout_start": candles[holdout_start]["timestamp"],
                            "holdout_end": candles[-1]["timestamp"]},
                "selected_parameters": parameters,
                "selection": "validation_return_percent_minus_half_max_drawdown_percent; minimum_3_closed_trades",
                "selection_fallback": "baseline_no_candidate_met_minimum_validation_trade_count" if not selected else None,
                "validation_candidates": sorted(rows, key=lambda row: row["selection_score"]
                    if row["selection_score"] is not None else -math.inf, reverse=True),
                "holdout": holdout,
                "risk_limits_are_fixed": True,
                "method": "chronological_parameter_selection_with_untouched_final_holdout_not_a_future_performance_guarantee"}
