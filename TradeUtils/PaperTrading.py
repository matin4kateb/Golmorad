"""Deterministic, SQLite-backed spot paper simulation.

This module has no Bit24 client dependency and cannot submit exchange requests.
Each decision uses candles up to (but excluding) the next candle, which is the
first simulated execution opportunity. Execution assumptions are explicit and
are stored with each order.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from Database.SQLiteUtils import SQLiteUtils, utc_now
from TradeUtils.StrategyAnalyzer import StrategyAnalyzer
from TradeUtils.TechnicalAnalyzer import TechnicalAnalyzer
from TradeUtils.RiskGate import RiskGate


PAPER_ACCOUNT_ID = "paper-default"
FEE_RATE = 0.001       # 10 bps per fill
HALF_SPREAD_RATE = 0.0005  # 5 bps adverse half-spread
SLIPPAGE_RATE = 0.001  # 10 bps adverse slippage
MAX_VOLUME_PARTICIPATION = 0.05


def _id(kind: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"golmorad:paper:{kind}:{value}"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class PaperTrader:
    """Run one walk-forward paper decision against saved candles."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()
        self.database.initialize()

    def _ensure_account(self, initial_cash: float) -> dict[str, Any]:
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Paper capital must be a finite positive amount")
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO paper_accounts(id, initial_cash, cash, updated_at) VALUES(?,?,?,?)",
                             (PAPER_ACCOUNT_ID, initial_cash, initial_cash, utc_now()))
                conn.execute("INSERT OR IGNORE INTO paper_control(id, stopped, updated_at) VALUES(1,0,?)",
                             (utc_now(),))
                row = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            elif not math.isclose(float(row["initial_cash"]), initial_cash):
                raise ValueError("Paper account already exists with different initial capital; reset is not implicit")
        return dict(row)

    def status(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            account = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            control = conn.execute("SELECT * FROM paper_control WHERE id=1").fetchone()
            orders = conn.execute("SELECT * FROM paper_orders ORDER BY fill_at DESC LIMIT 50").fetchall()
            events = conn.execute("SELECT * FROM paper_events ORDER BY created_at DESC LIMIT 50").fetchall()
        account_data = dict(account) if account else None
        if account_data:
            last_order = orders[0] if orders else None
            mark_time = None
            if last_order:
                with self.database.connection() as conn:
                    decision = conn.execute("SELECT interval FROM paper_decisions WHERE id=?",
                                            (last_order["decision_id"],)).fetchone()
                latest = self.database.get_candles(last_order["symbol"], decision["interval"], limit=1) if decision else []
            else:
                latest = []
            mark = float(latest[-1]["close"]) if latest else None
            mark_time = latest[-1]["timestamp"] if latest else None
            account_data["mark_price"] = mark
            account_data["unrealized_pnl"] = ((mark - account_data["average_entry"]) * account_data["position_quantity"]
                                                if mark is not None else None)
            account_data["equity"] = account_data["cash"] + (mark * account_data["position_quantity"] if mark is not None else 0)
            account_data["total_pnl"] = account_data["realized_pnl"] + (account_data["unrealized_pnl"] or 0)
            account_data["risk_consumption"] = self._risk_metrics(account_data["equity"],
                                                                   mark_time)
        return {
            "mode": "paper",
            "stopped": bool(control["stopped"]) if control else False,
            "stop_reason": control["reason"] if control else None,
            "account": account_data,
            "orders": [dict(row) for row in orders],
            "events": [{**dict(row), "payload": json.loads(row["payload_json"])} for row in events],
        }

    def stop(self, reason: str = "operator_stop") -> None:
        with self.database.connection() as conn:
            conn.execute("INSERT INTO paper_control(id, stopped, reason, updated_at) VALUES(1,1,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET stopped=1, reason=excluded.reason, updated_at=excluded.updated_at",
                         (reason[:160], utc_now()))
        self._event("stop", {"reason": reason[:160]})

    def resume(self) -> None:
        with self.database.connection() as conn:
            conn.execute("INSERT INTO paper_control(id, stopped, reason, updated_at) VALUES(1,0,NULL,?) "
                         "ON CONFLICT(id) DO UPDATE SET stopped=0, reason=NULL, updated_at=excluded.updated_at",
                         (utc_now(),))
        self._event("resume", {"operator_action": True})

    def _event(self, event_type: str, payload: dict[str, Any], decision_id: str | None = None,
               order_id: str | None = None) -> None:
        event_id = str(uuid.uuid4())
        with self.database.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO paper_events(id,event_type,created_at,decision_id,order_id,payload_json) "
                         "VALUES(?,?,?,?,?,?)", (event_id, event_type, utc_now(), decision_id, order_id, _json(payload)))

    def _record_mark(self, marked_at: str, symbol: str, cash: float, quantity: float,
                     mark_price: float) -> dict[str, float]:
        equity = cash + quantity * mark_price
        mark_id = _id("mark", f"{symbol}:{marked_at}")
        with self.database.connection() as conn:
            conn.execute("INSERT INTO paper_account_marks(id,marked_at,symbol,equity,cash,position_value) "
                         "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET equity=excluded.equity, "
                         "cash=excluded.cash,position_value=excluded.position_value",
                         (mark_id, marked_at, symbol, equity, cash, quantity * mark_price))
        return self._risk_metrics(equity, marked_at)

    def _risk_metrics(self, equity: float, marked_at: str | None) -> dict[str, float]:
        if not marked_at:
            return {"daily_loss": 0.0, "drawdown": 0.0}
        day = marked_at[:10]
        with self.database.connection() as conn:
            rows = conn.execute("SELECT marked_at,equity FROM paper_account_marks ORDER BY marked_at").fetchall()
        daily = [float(row["equity"]) for row in rows if str(row["marked_at"]).startswith(day)]
        peak = max([float(row["equity"]) for row in rows] + [equity])
        day_open_equity = daily[0] if daily else equity
        return {"daily_loss": max(0.0, day_open_equity - equity),
                "drawdown": max(0.0, peak - equity)}

    def _protective_exit(self, symbol: str, decision_at: str, execution_candle: dict[str, Any],
                         decision_time: datetime, execution_time: datetime) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            account = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            previous_buy = conn.execute("SELECT * FROM paper_orders WHERE symbol=? AND side='buy' "
                                        "ORDER BY fill_at DESC LIMIT 1", (symbol,)).fetchone()
        if not account or float(account["position_quantity"]) <= 0:
            return None
        if not previous_buy:
            return {"status": "blocked", "reason": "An open simulated position has no auditable stop/target order."}
        details = json.loads(previous_buy["details_json"])
        stop, target = details.get("stop_price"), details.get("target_price")
        if stop is None or target is None:
            return {"status": "blocked", "reason": "Open simulated position has incomplete protective levels."}
        try:
            open_price, high, low = (float(execution_candle[name]) for name in ("open", "high", "low"))
            stop, target = float(stop), float(target)
            volume = float(execution_candle["volume"])
            if not all(math.isfinite(v) and v > 0 for v in (open_price, high, low, stop, target)) or volume <= 0:
                return {"status": "blocked", "reason": "Protective exit candle has invalid price or liquidity."}
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"status": "blocked", "reason": "Protective exit candle is incomplete."}
        hit_stop, hit_target = low <= stop, high >= target
        if not hit_stop and not hit_target:
            return None
        reason = "stop" if hit_stop else "target"  # If both touched, use the adverse outcome.
        level = stop if hit_stop else target
        raw_exit = min(open_price, level) if hit_stop and open_price < level else level
        fill_price = raw_exit * (1 - HALF_SPREAD_RATE - SLIPPAGE_RATE)
        quantity = min(float(account["position_quantity"]), volume * MAX_VOLUME_PARTICIPATION)
        if quantity <= 0:
            return {"status": "blocked", "reason": "No exit liquidity is available."}
        fee = quantity * fill_price * FEE_RATE
        proceeds = quantity * fill_price
        realized = proceeds - fee - quantity * float(account["average_entry"])
        order_id = _id("protective-exit", f"{previous_buy['id']}:{execution_candle['timestamp']}:{quantity}")
        status = "partially_filled" if quantity + 1e-12 < float(account["position_quantity"]) else "filled"
        exit_details = {"reason": reason, "source_entry_order": previous_buy["id"],
                        "stop_price": stop, "target_price": target,
                        "assumptions": {"fee_rate": FEE_RATE, "half_spread_rate": HALF_SPREAD_RATE,
                                        "slippage_rate": SLIPPAGE_RATE,
                                        "max_volume_participation": MAX_VOLUME_PARTICIPATION}}
        with self.database.connection() as conn:
            current = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            remaining = max(0.0, float(current["position_quantity"]) - quantity)
            conn.execute("INSERT INTO paper_orders(id,decision_id,symbol,side,status,decision_at,fill_at,requested_quantity,filled_quantity,fill_price,fee,spread_cost,slippage_cost,latency_seconds,liquidity_limit,realized_pnl,details_json) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (order_id, previous_buy["decision_id"], symbol, "sell", status, decision_at,
                          execution_candle["timestamp"], float(account["position_quantity"]), quantity,
                          fill_price, fee, quantity * raw_exit * HALF_SPREAD_RATE,
                          quantity * raw_exit * SLIPPAGE_RATE,
                          max(0.0, (execution_time - decision_time).total_seconds()),
                          volume * MAX_VOLUME_PARTICIPATION, realized, _json(exit_details)))
            conn.execute("UPDATE paper_accounts SET cash=cash+?,position_quantity=?,average_entry=?,realized_pnl=realized_pnl+?,updated_at=? WHERE id=?",
                         (proceeds - fee, remaining, float(account["average_entry"]) if remaining else 0.0,
                          realized, utc_now(), PAPER_ACCOUNT_ID))
        current_cash = float(account["cash"]) + proceeds - fee
        self._record_mark(execution_candle["timestamp"], symbol, current_cash, remaining,
                          float(execution_candle["close"]))
        outcome = {"status": status, "side": "sell", "reason": reason, "order_id": order_id,
                   "filled_quantity": quantity, "remaining_quantity": remaining,
                   "fill_price": fill_price, "fee": fee, "realized_pnl": realized}
        return outcome

    def run_once(self, symbol: str = "BTC-USD", interval: str = "1d",
                 initial_cash: float = 10_000.0, *,
                 auto_optimize: bool = False) -> dict[str, Any]:
        account = self._ensure_account(initial_cash)
        with self.database.connection() as conn:
            control = conn.execute("SELECT stopped, reason FROM paper_control WHERE id=1").fetchone()
        if control and control["stopped"]:
            return {"status": "stopped", "reason": control["reason"]}

        candles = self.database.get_candles(symbol, interval, limit=501)
        # Yahoo can return the current, still-forming candle. Using it as a
        # simulated fill bar would assign a fill at its historical open before
        # the application actually had the complete bar's information.
        if candles and not TechnicalAnalyzer._is_closed_candle(str(candles[-1].get("timestamp", "")), interval):
            candles = candles[:-1]
        if len(candles) < 51:
            return {"status": "no_trade", "reason": "At least 51 completed candles are needed for a next-candle simulation."}
        decision_candles, execution_candle = candles[:-1], candles[-1]
        strategy_parameters = None
        optimization = None
        if auto_optimize:
            from TradeUtils.ParameterOptimizer import ParameterOptimizer

            # Exclude the candle being analyzed and the future execution candle
            # from parameter selection; only prior history may choose settings.
            optimization = ParameterOptimizer(self.database).run(
                symbol, interval, float(initial_cash), candles=decision_candles[:-1])
            if optimization.get("status") != "complete":
                return {"status": "no_trade", "reason": "Parameter selection could not produce a validated configuration.",
                        "optimization": optimization}
            strategy_parameters = optimization["selected_parameters"]
        report = StrategyAnalyzer(TechnicalAnalyzer(self.database)).analyze(
            symbol, interval, candles=decision_candles, parameters=strategy_parameters)
        decision_at = report.get("as_of")
        execution_at = execution_candle.get("timestamp")
        try:
            decision_time = datetime.fromisoformat(str(decision_at).replace("Z", "+00:00"))
            execution_time = datetime.fromisoformat(str(execution_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return {"status": "no_trade", "reason": "Decision or execution timestamp is invalid."}
        if decision_time.tzinfo is None or execution_time.tzinfo is None or execution_time <= decision_time:
            return {"status": "no_trade", "reason": "No later candle is available for a look-ahead-safe fill."}
        if report.get("freshness", {}).get("status") != "fresh":
            self.stop("stale_or_unknown_decision_data")
            return {"status": "no_trade", "reason": "Decision data is stale or freshness is unknown."}
        candle_error = TechnicalAnalyzer._validate_candles([execution_candle])
        execution_freshness = TechnicalAnalyzer._freshness(str(execution_at), interval)
        if candle_error or execution_freshness.get("status") != "fresh":
            self.stop("invalid_or_stale_execution_data")
            return {"status": "no_trade", "reason": candle_error or
                    "Next-candle execution data is stale or freshness is unknown."}

        decision_id = _id("decision", f"{symbol}:{interval}:{decision_at}")
        with self.database.connection() as conn:
            exists = conn.execute("SELECT id FROM paper_decisions WHERE symbol=? AND interval=? AND candle_timestamp=?",
                                  (symbol, interval, decision_at)).fetchone()
            if exists:
                return {"status": "duplicate", "decision_id": exists["id"], "reason": "This candle decision was already processed."}
            cursor = conn.execute("INSERT OR IGNORE INTO paper_decisions(id,symbol,interval,candle_timestamp,signal,reason,source,data_json,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (decision_id, symbol, interval, decision_at,
                         report["signal"], report["reason"], report.get("source"), _json(report), utc_now()))
            if cursor.rowcount != 1:
                return {"status": "duplicate", "decision_id": decision_id,
                        "reason": "This candle decision was concurrently processed."}

        result: dict[str, Any] = {"status": "no_trade", "decision_id": decision_id,
                                  "signal": report["signal"], "reason": report["reason"],
                                  "decision_at": decision_at, "execution_at": execution_at,
                                  "source": report.get("source")}
        if optimization is not None:
            result["selected_parameters"] = strategy_parameters
            result["parameter_holdout"] = optimization.get("holdout", {}).get("return_percent")
        exit_result = self._protective_exit(symbol, decision_at, execution_candle,
                                            decision_time, execution_time)
        if exit_result:
            result.update(exit_result)
            if exit_result.get("status") == "blocked":
                self.stop("protective_exit_data_or_state_error")
            self._event("protective_exit_blocked" if exit_result.get("status") == "blocked" else
                        "protective_exit", result, decision_id, exit_result.get("order_id"))
            return result
        plan = report.get("trade_plan")
        if report.get("signal") != "buy" or not plan:
            self._record_mark(execution_at, symbol, float(account["cash"]),
                              float(account["position_quantity"]), float(execution_candle["close"]))
            self._event("decision", result, decision_id)
            return result
        with self.database.connection() as conn:
            current_position = conn.execute("SELECT position_quantity FROM paper_accounts WHERE id=?",
                                            (PAPER_ACCOUNT_ID,)).fetchone()
        if current_position and float(current_position["position_quantity"]) > 0:
            result.update(reason="An open paper position already exists; scaling in is disabled.")
            self._event("risk_rejection", result, decision_id)
            return result

        try:
            next_open = float(execution_candle["open"])
            volume = float(execution_candle["volume"])
            stop = float(plan["stop_price"])
            if not all(math.isfinite(v) and v > 0 for v in (next_open, stop)) or not math.isfinite(volume) or volume <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            result.update(status="no_trade", reason="Next candle price or liquidity is invalid.")
            self._event("rejected", result, decision_id)
            return result

        fill_price = next_open * (1 + HALF_SPREAD_RATE + SLIPPAGE_RATE)
        fee_per_unit = fill_price * FEE_RATE
        risk_per_unit = max(fill_price - stop, 0) + fee_per_unit
        if risk_per_unit <= 0:
            result.update(reason="Stop distance and costs do not produce positive unit risk.")
            self._event("rejected", result, decision_id)
            return result
        cash = float(account["cash"])
        risk_budget = float(account["initial_cash"]) * 0.01
        risk_quantity = risk_budget / risk_per_unit
        cash_quantity = cash / (fill_price * (1 + FEE_RATE))
        liquidity_limit = volume * MAX_VOLUME_PARTICIPATION
        current_quantity = float(account["position_quantity"])
        current_exposure = current_quantity * next_open
        limits = RiskGate().limits
        exposure_room = max(0.0, float(account["initial_cash"]) * limits.max_total_exposure_percent / 100
                            - current_exposure)
        requested = min(risk_quantity, cash_quantity, limits.max_order_value / fill_price,
                        exposure_room / fill_price)
        filled = min(requested, liquidity_limit)
        if filled <= 0:
            result.update(reason="Available paper cash or next-candle liquidity is zero.")
            self._event("rejected", result, decision_id)
            return result

        with self.database.connection() as conn:
            daily_orders = int(conn.execute("SELECT COUNT(*) FROM paper_orders WHERE fill_at>=?",
                (execution_time.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00"),)).fetchone()[0])
        gate_result = RiskGate().evaluate(
            initial_cash=float(account["initial_cash"]), cash=cash,
            current_exposure=current_exposure, price=fill_price, stop_price=stop,
            quantity=filled, fee_rate=FEE_RATE,
            daily_loss=self._record_mark(execution_at, symbol, cash, current_quantity, next_open)["daily_loss"],
            drawdown=self._risk_metrics(cash + current_quantity * next_open, execution_at)["drawdown"],
            trades_today=daily_orders, data_fresh=True)
        if not gate_result["allowed"]:
            result.update(reason=gate_result["reason"], risk_gate=gate_result)
            self.stop("risk_limit:" + str(gate_result["reason"]))
            self._event("risk_rejection", result, decision_id)
            return result

        fee = filled * fee_per_unit
        gross = filled * fill_price
        spread_cost = filled * next_open * HALF_SPREAD_RATE
        slippage_cost = filled * next_open * SLIPPAGE_RATE
        status = "partially_filled" if filled + 1e-12 < requested else "filled"
        order_id = _id("order", decision_id)
        details = {"assumptions": {"fee_rate": FEE_RATE, "half_spread_rate": HALF_SPREAD_RATE,
                                    "slippage_rate": SLIPPAGE_RATE,
                                    "max_volume_participation": MAX_VOLUME_PARTICIPATION,
                                    "risk_limits": gate_result["limits"]},
                   "stop_price": stop, "target_price": float(plan["target_price"]), "cash_before": cash}
        insufficient_cash = False
        with self.database.connection() as conn:
            current = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (PAPER_ACCOUNT_ID,)).fetchone()
            if current is None or float(current["cash"]) + 1e-9 < gross + fee:
                insufficient_cash = True
            else:
                conn.execute("INSERT INTO paper_orders(id,decision_id,symbol,side,status,decision_at,fill_at,requested_quantity,filled_quantity,fill_price,fee,spread_cost,slippage_cost,latency_seconds,liquidity_limit,details_json) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (order_id, decision_id, symbol, "buy", status, decision_at, execution_at,
                              requested, filled, fill_price, fee, spread_cost, slippage_cost,
                              max(0.0, (execution_time-decision_time).total_seconds()), liquidity_limit, _json(details)))
                old_qty, old_avg = float(current["position_quantity"]), float(current["average_entry"])
                new_qty = old_qty + filled
                new_avg = ((old_qty * old_avg) + gross + fee) / new_qty
                conn.execute("UPDATE paper_accounts SET cash=cash-?,position_quantity=?,average_entry=?,updated_at=? WHERE id=?",
                             (gross + fee, new_qty, new_avg, utc_now(), PAPER_ACCOUNT_ID))
        if insufficient_cash:
            result.update(reason="Paper account cash changed or is insufficient.")
            self._event("rejected", result, decision_id)
            return result
        self._record_mark(execution_at, symbol, float(current["cash"]) - gross - fee,
                          new_qty, float(execution_candle["close"]))
        result.update(status=status, order_id=order_id, requested_quantity=requested,
                      filled_quantity=filled, fill_price=fill_price, fee=fee,
                      spread_cost=spread_cost, slippage_cost=slippage_cost,
                      liquidity_limit=liquidity_limit, latency_seconds=max(0.0, (execution_time-decision_time).total_seconds()))
        result["risk_gate"] = gate_result
        self._event("order_fill", result, decision_id, order_id)
        exit_result = self._protective_exit(symbol, decision_at, execution_candle,
                                            decision_time, execution_time)
        if exit_result:
            result["protective_exit"] = exit_result
            self._event("protective_exit", exit_result, decision_id, exit_result.get("order_id"))
        return result
