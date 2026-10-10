"""Position sizing helpers for simple, unleveraged spot trades."""

from __future__ import annotations

import math
from typing import Any


class RiskManager:
    """Calculate position size from a fixed account-risk budget.

    This helper does not place orders and does not account for fees, slippage,
    leverage, or exchange-specific minimums.
    """

    @staticmethod
    def calculate_position_size(account_balance: float, entry_price: float,
                                stop_price: float, risk_percent: float = 1.0) -> dict[str, Any]:
        values = (account_balance, entry_price, stop_price, risk_percent)
        if any(not isinstance(value, (int, float)) for value in values):
            raise TypeError("All sizing inputs must be numbers")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Sizing inputs must be finite numbers")
        if account_balance <= 0 or entry_price <= 0 or stop_price <= 0:
            raise ValueError("Balance and prices must be positive")
        if not 0 < risk_percent <= 100:
            raise ValueError("risk_percent must be greater than 0 and at most 100")
        if stop_price >= entry_price:
            raise ValueError("For a spot long, stop_price must be below entry_price")

        risk_amount = account_balance * risk_percent / 100
        unit_risk = entry_price - stop_price
        risk_limited_quantity = risk_amount / unit_risk
        affordable_quantity = account_balance / entry_price
        quantity = min(risk_limited_quantity, affordable_quantity)
        exposure = quantity * entry_price
        actual_risk_amount = quantity * unit_risk
        return {
            "account_balance": account_balance,
            "risk_percent": risk_percent,
            "risk_amount": risk_amount,
            "actual_risk_amount": actual_risk_amount,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "quantity": quantity,
            "exposure": exposure,
            "exposure_percent": exposure / account_balance * 100,
            "actual_risk_percent": actual_risk_amount / account_balance * 100,
            "cash_limited": quantity < risk_limited_quantity,
        }
