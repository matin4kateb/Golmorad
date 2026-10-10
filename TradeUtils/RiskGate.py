"""Independent, deterministic fail-closed gate for simulated order sizing."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RiskLimits:
    max_trade_risk_percent: float = 1.0
    max_order_value: float = 250.0
    max_total_exposure_percent: float = 25.0
    max_daily_loss_percent: float = 3.0
    max_drawdown_percent: float = 10.0
    max_trades_per_day: int = 5
    max_leverage: float = 1.0
    max_stop_distance_percent: float = 20.0


class RiskGate:
    """Apply fixed bounds before simulated execution; missing inputs reject."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def evaluate(self, *, initial_cash: float, cash: float, current_exposure: float,
                 price: float, stop_price: float, quantity: float, fee_rate: float,
                 daily_loss: float, drawdown: float, trades_today: int,
                 leverage: float = 1.0, data_fresh: bool = False) -> dict[str, Any]:
        values = (initial_cash, cash, current_exposure, price, stop_price, quantity,
                  fee_rate, daily_loss, drawdown, leverage)
        if any(value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in values) or trades_today is None:
            return {"allowed": False, "reason": "Required risk data is missing or invalid.",
                    "limits": asdict(self.limits)}
        if (isinstance(trades_today, bool) or not isinstance(trades_today, int) or trades_today < 0
                or fee_rate < 0 or daily_loss < 0 or drawdown < 0):
            return {"allowed": False, "reason": "Risk counters or costs are outside valid bounds.",
                    "limits": asdict(self.limits)}
        if not data_fresh:
            return {"allowed": False, "reason": "Market data is not confirmed fresh.", "limits": asdict(self.limits)}
        if initial_cash <= 0 or cash < 0 or current_exposure < 0 or price <= 0 or stop_price <= 0 or quantity <= 0:
            return {"allowed": False, "reason": "Account, price, stop, or quantity is outside valid bounds.",
                    "limits": asdict(self.limits)}
        if stop_price >= price:
            return {"allowed": False, "reason": "Stop must be below the entry price for spot long exposure.",
                    "limits": asdict(self.limits)}
        if leverage > self.limits.max_leverage or leverage < 1:
            return {"allowed": False, "reason": "Leverage exceeds the configured spot-only limit.",
                    "limits": asdict(self.limits)}
        if trades_today >= self.limits.max_trades_per_day:
            return {"allowed": False, "reason": "Daily trade count limit reached.", "limits": asdict(self.limits)}
        if daily_loss >= initial_cash * self.limits.max_daily_loss_percent / 100:
            return {"allowed": False, "reason": "Daily loss limit reached.", "limits": asdict(self.limits)}
        if drawdown >= initial_cash * self.limits.max_drawdown_percent / 100:
            return {"allowed": False, "reason": "Drawdown limit reached.", "limits": asdict(self.limits)}
        order_value = quantity * price
        projected_exposure = current_exposure + order_value
        risk_amount = quantity * ((price - stop_price) + price * fee_rate)
        stop_distance_percent = (price - stop_price) / price * 100
        if order_value > self.limits.max_order_value:
            reason = "Per-order value limit exceeded."
        elif projected_exposure > initial_cash * self.limits.max_total_exposure_percent / 100:
            reason = "Total exposure limit exceeded."
        elif risk_amount > initial_cash * self.limits.max_trade_risk_percent / 100:
            reason = "Per-trade risk limit exceeded."
        elif stop_distance_percent > self.limits.max_stop_distance_percent:
            reason = "Stop distance exceeds its configured maximum."
        elif quantity * price * (1 + fee_rate) > cash:
            reason = "Available simulated cash is insufficient."
        else:
            reason = None
        return {"allowed": reason is None, "reason": reason or "All fixed risk limits passed.",
                "order_value": order_value, "projected_exposure": projected_exposure,
                "risk_amount": risk_amount, "stop_distance_percent": stop_distance_percent,
                "limits": asdict(self.limits)}
