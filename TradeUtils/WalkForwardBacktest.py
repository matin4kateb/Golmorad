"""Offline expanding-window backtest, separate from the live/paper runners."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from Database.SQLiteUtils import SQLiteUtils
from TradeUtils.RiskGate import RiskGate
from TradeUtils.StrategyAnalyzer import StrategyAnalyzer
from TradeUtils.TechnicalAnalyzer import TechnicalAnalyzer


FEE_RATE = 0.001
HALF_SPREAD_RATE = 0.0005
SLIPPAGE_RATE = 0.001
MAX_VOLUME_PARTICIPATION = 0.05


class WalkForwardBacktest:
    """Use expanding historical prefixes and strictly later execution candles."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()

    def run(self, symbol: str, interval: str, initial_cash: float,
            folds: int = 5, *, parameters: dict[str, float] | None = None,
            candles: list[dict[str, Any]] | None = None,
            test_start_index: int = 51) -> dict[str, Any]:
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Backtest capital must be a finite positive amount")
        if folds < 2 or folds > 10:
            raise ValueError("folds must be between 2 and 10")
        candles = candles if candles is not None else self.database.get_candles(symbol, interval, limit=2000)
        if candles and not TechnicalAnalyzer._is_closed_candle(str(candles[-1].get("timestamp", "")), interval):
            candles = candles[:-1]
        invalid = TechnicalAnalyzer._validate_candles(candles)
        if invalid:
            return {"status": "invalid_data", "reason": invalid, "trades": [], "folds": []}
        minimum = 52
        if len(candles) < minimum:
            return {"status": "insufficient_data", "reason": f"At least {minimum} candles are required.",
                    "observations": len(candles), "trades": [], "folds": []}

        if isinstance(test_start_index, bool) or not isinstance(test_start_index, int) or not 51 <= test_start_index < len(candles):
            return {"status": "insufficient_data", "reason": "The requested test window is outside the available candle range.",
                    "observations": len(candles), "trades": [], "folds": []}
        test_bars = len(candles) - test_start_index
        fold_size = max(1, math.ceil(test_bars / folds))
        cash = initial_cash
        position: dict[str, float] | None = None
        trades: list[dict[str, Any]] = []
        fold_rows: dict[int, dict[str, Any]] = {}
        equity_curve: list[float] = [initial_cash]
        gate = RiskGate()
        daily_date: str | None = None
        daily_start_equity = initial_cash
        trades_today = 0
        peak_equity = initial_cash

        for execution_index in range(test_start_index, len(candles)):
            fold_number = min(folds, (execution_index - test_start_index) // fold_size + 1)
            fold_rows.setdefault(fold_number, {
                "fold": fold_number, "training_start": candles[0]["timestamp"],
                "training_end": candles[execution_index - 1]["timestamp"],
                "test_start": candles[execution_index]["timestamp"],
                "test_end": candles[execution_index]["timestamp"], "test_bars": 0,
            })
            fold_rows[fold_number]["test_end"] = candles[execution_index]["timestamp"]
            fold_rows[fold_number]["test_bars"] += 1
            candle = candles[execution_index]
            open_price, high, low, close, volume = (float(candle[k]) for k in ("open", "high", "low", "close", "volume"))
            current_date = str(candle["timestamp"])[:10]
            marked_equity = cash + (position["quantity"] * float(candles[execution_index - 1]["close"])
                                    if position else 0.0)
            if current_date != daily_date:
                daily_date = current_date
                daily_start_equity = marked_equity
                trades_today = 0
            peak_equity = max(peak_equity, marked_equity)
            daily_loss = max(0.0, daily_start_equity - marked_equity)
            drawdown = max(0.0, peak_equity - marked_equity)

            # Resolve existing bracket orders conservatively if both levels are in one bar.
            exited_this_bar = False
            if position:
                hit_stop, hit_target = low <= position["stop"], high >= position["target"]
                if hit_stop or hit_target:
                    exit_level = position["stop"] if hit_stop else position["target"]
                    raw_exit = min(open_price, exit_level) if hit_stop and open_price < exit_level else exit_level
                    exit_price = raw_exit * (1 - HALF_SPREAD_RATE - SLIPPAGE_RATE)
                    exit_fee = position["quantity"] * exit_price * FEE_RATE
                    proceeds = position["quantity"] * exit_price - exit_fee
                    cash += proceeds
                    pnl = proceeds - position["entry_cost"]
                    position["exit_at"] = candle["timestamp"]
                    position["exit_price"] = exit_price
                    position["exit_fee"] = exit_fee
                    position["pnl"] = pnl
                    position["exit_reason"] = "stop" if hit_stop else "target"
                    trades.append(position)
                    position = None
                    exited_this_bar = True

            # No same-bar signal: this report only sees candles strictly before this fill candle.
            if position is None and not exited_this_bar and volume > 0:
                parsed = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
                report = StrategyAnalyzer(TechnicalAnalyzer(self.database)).analyze(
                    symbol, interval, candles=candles[:execution_index], reference_time=parsed,
                    parameters=parameters)
                plan = report.get("trade_plan")
                if report.get("signal") == "buy" and plan:
                    entry = open_price * (1 + HALF_SPREAD_RATE + SLIPPAGE_RATE)
                    stop, target = float(plan["stop_price"]), float(plan["target_price"])
                    fee_per_unit = entry * FEE_RATE
                    risk_unit = (entry - stop) + fee_per_unit
                    liquidity = volume * MAX_VOLUME_PARTICIPATION
                    quantity = min(initial_cash * gate.limits.max_trade_risk_percent / 100 / risk_unit,
                                   cash / (entry * (1 + FEE_RATE)), gate.limits.max_order_value / entry,
                                   initial_cash * gate.limits.max_total_exposure_percent / 100 / entry,
                                   liquidity) if risk_unit > 0 else 0
                    gate_result = gate.evaluate(initial_cash=initial_cash, cash=cash,
                        current_exposure=0, price=entry, stop_price=stop, quantity=quantity,
                        fee_rate=FEE_RATE, daily_loss=daily_loss, drawdown=drawdown,
                        trades_today=trades_today,
                        data_fresh=report.get("freshness", {}).get("status") == "fresh")
                    if quantity > 0 and gate_result["allowed"]:
                        fee = quantity * fee_per_unit
                        entry_cost = quantity * entry + fee
                        cash -= entry_cost
                        trades_today += 1
                        position = {"symbol": symbol, "decision_at": report["as_of"],
                                    "fill_at": candle["timestamp"], "quantity": quantity,
                                    "entry_price": entry, "entry_fee": fee, "entry_cost": entry_cost,
                                    "stop": stop, "target": target, "spread_cost": quantity * open_price * HALF_SPREAD_RATE,
                                    "slippage_cost": quantity * open_price * SLIPPAGE_RATE,
                                    "fee_rate": FEE_RATE, "half_spread_rate": HALF_SPREAD_RATE,
                                    "slippage_rate": SLIPPAGE_RATE, "fold": fold_number}
                        # High/low are later than the open, so same-candle bracket outcomes are usable.
                        hit_stop, hit_target = low <= stop, high >= target
                        if hit_stop or hit_target:
                            exit_level = stop if hit_stop else target
                            raw_exit = min(open_price, exit_level) if hit_stop and open_price < exit_level else exit_level
                            exit_price = raw_exit * (1 - HALF_SPREAD_RATE - SLIPPAGE_RATE)
                            exit_fee = quantity * exit_price * FEE_RATE
                            proceeds = quantity * exit_price - exit_fee
                            cash += proceeds
                            position.update(exit_at=candle["timestamp"], exit_price=exit_price,
                                            exit_fee=exit_fee, pnl=proceeds-entry_cost,
                                            exit_reason="stop" if hit_stop else "target")
                            trades.append(position)
                            position = None

            equity_curve.append(cash + (position["quantity"] * close if position else 0))

        final_mark = float(candles[-1]["close"])
        final_equity = cash + (position["quantity"] * final_mark if position else 0)
        if position:
            position = {**position, "status": "open_marked_to_market", "mark_price": final_mark,
                        "unrealized_pnl": position["quantity"] * final_mark - position["entry_cost"]}
            trades.append(position)
        peak = initial_cash
        max_drawdown = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        return {"status": "complete", "symbol": symbol, "interval": interval,
                "initial_cash": initial_cash, "final_equity": final_equity,
                "pnl": final_equity - initial_cash,
                "return_percent": (final_equity / initial_cash - 1) * 100,
                "max_drawdown_percent": max_drawdown, "trades": trades,
                "strategy_parameters": parameters or {"stop_atr_multiple": 2.0,
                    "reward_risk_ratio": 2.0, "rsi_lower": 30.0, "rsi_upper": 70.0},
                "folds": list(fold_rows.values()),
                "cost_assumptions": {"fee_rate": FEE_RATE, "half_spread_rate": HALF_SPREAD_RATE,
                                     "slippage_rate": SLIPPAGE_RATE,
                                     "max_volume_participation": MAX_VOLUME_PARTICIPATION},
                "method": "expanding_walk_forward_rule_based_not_a_future_performance_guarantee"}
