"""Technical indicators computed from persisted OHLCV candles."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from Database.SQLiteUtils import SQLiteUtils


class TechnicalAnalyzer:
    """Read stored Bitcoin candles and calculate common, deterministic indicators."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()

    @staticmethod
    def _sma(values: list[float], period: int) -> float | None:
        return sum(values[-period:]) / period if len(values) >= period else None

    @staticmethod
    def _ema(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None
        result = sum(values[:period]) / period
        alpha = 2 / (period + 1)
        for value in values[period:]:
            result = value * alpha + result * (1 - alpha)
        return result

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float | None:
        if len(values) <= period:
            return None
        changes = [values[i] - values[i - 1] for i in range(1, len(values))]
        gains = [max(change, 0) for change in changes]
        losses = [max(-change, 0) for change in changes]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for gain, loss in zip(gains[period:], losses[period:]):
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            return 100.0 if avg_gain else 50.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    @staticmethod
    def _freshness(timestamp: str, interval: str,
                   reference_time: datetime | None = None) -> dict[str, Any]:
        """Classify candle age with a conservative grace period for candle opens."""
        durations = {
            "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
            "60m": 3600, "90m": 5400, "1h": 3600, "1d": 86400,
            "5d": 432000, "1wk": 604800, "1mo": 2592000, "3mo": 7776000,
        }
        duration = durations.get(interval)
        if duration is None:
            return {"status": "unknown", "age_seconds": None,
                    "max_age_seconds": None}
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            reference = reference_time or datetime.now(timezone.utc)
            if reference.tzinfo is None:
                return {"status": "unknown", "age_seconds": None,
                        "max_age_seconds": duration * 2}
            age = (reference.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return {"status": "unknown", "age_seconds": None,
                    "max_age_seconds": duration * 2}
        if age < -300:
            return {"status": "unknown", "age_seconds": int(age),
                    "max_age_seconds": duration * 2}
        age_seconds = max(0, int(age))
        # Daily Yahoo candles are timestamped at the start of the UTC day.
        max_age_seconds = max(duration * 2, 48 * 60 * 60 if interval == "1d" else 0)
        return {"status": "fresh" if age_seconds <= max_age_seconds else "stale",
                "age_seconds": age_seconds, "max_age_seconds": max_age_seconds}

    @staticmethod
    def _is_closed_candle(timestamp: str, interval: str,
                          reference_time: datetime | None = None) -> bool:
        """Return whether an interval candle's full time window has elapsed.

        Paper fills and historical evaluation must not treat the currently
        forming bar as a completed next-candle execution opportunity.
        Unknown interval semantics fail closed.
        """
        durations = {
            "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
            "60m": 3600, "90m": 5400, "1h": 3600, "1d": 86400,
            "5d": 432000, "1wk": 604800, "1mo": 2592000, "3mo": 7776000,
        }
        try:
            opened = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            now = reference_time or datetime.now(timezone.utc)
            if now.tzinfo is None:
                return False
            now = now.astimezone(timezone.utc)
            opened = opened.astimezone(timezone.utc)
            if interval in {"1mo", "3mo"}:
                months = 1 if interval == "1mo" else 3
                if opened.day != 1 or (interval == "3mo" and (opened.month - 1) % 3):
                    return False
                next_month = opened.year * 12 + (opened.month - 1) + months
                boundary = datetime(next_month // 12, next_month % 12 + 1, 1, tzinfo=timezone.utc)
                return now >= boundary
            duration = durations.get(interval)
            return duration is not None and (now - opened).total_seconds() >= duration
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _validate_candles(candles: list[dict[str, Any]]) -> str | None:
        """Reject malformed, duplicate, or unordered source records before analysis."""
        previous_timestamp: str | None = None
        for index, candle in enumerate(candles):
            timestamp = candle.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp:
                return f"Candle {index + 1} has no valid timestamp."
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return f"Candle {index + 1} has an invalid timestamp."
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                return "Candle timestamps are duplicated or out of order."
            previous_timestamp = timestamp

            try:
                values = {key: float(candle[key]) for key in ("open", "high", "low", "close", "volume")}
            except (KeyError, TypeError, ValueError, OverflowError):
                return f"Candle {index + 1} is missing a usable OHLCV value."
            if any(not math.isfinite(value) for value in values.values()):
                return f"Candle {index + 1} contains a non-finite OHLCV value."
            prices = (values["open"], values["high"], values["low"], values["close"])
            if (min(prices) <= 0 or values["volume"] < 0
                    or values["high"] < max(values["open"], values["close"], values["low"])
                    or values["low"] > min(values["open"], values["close"], values["high"])):
                return f"Candle {index + 1} violates positive-price or OHLCV bounds."
        return None

    def analyze(self, symbol: str = "BTC-USD", interval: str = "1d", limit: int = 500,
                *, candles: list[dict[str, Any]] | None = None,
                reference_time: datetime | None = None) -> dict[str, Any]:
        candles = candles if candles is not None else self.database.get_candles(symbol, interval, limit)
        if not candles:
            return {"symbol": symbol, "interval": interval, "observations": 0,
                    "data_quality": {"status": "empty", "reason": "No candles are stored."},
                    "indicators": {}}
        quality_error = self._validate_candles(candles)
        if quality_error:
            return {"symbol": symbol, "interval": interval, "observations": len(candles),
                    "as_of": candles[-1].get("timestamp"),
                    "source": candles[-1].get("source"),
                    "freshness": self._freshness(candles[-1].get("timestamp"), interval, reference_time),
                    "data_quality": {"status": "invalid", "reason": quality_error},
                    "indicators": {}}
        closes = [float(row["close"]) for row in candles]
        highs = [float(row["high"]) for row in candles]
        lows = [float(row["low"]) for row in candles]
        previous = closes[-2] if len(closes) > 1 else None
        latest = candles[-1]
        returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
        true_ranges = [highs[0] - lows[0]]
        for i in range(1, len(candles)):
            true_ranges.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        recent_returns = returns[-20:]
        return_mean = sum(recent_returns) / len(recent_returns) if recent_returns else 0
        indicators = {
            "sma_20": self._sma(closes, 20), "sma_50": self._sma(closes, 50),
            "sma_200": self._sma(closes, 200), "ema_12": self._ema(closes, 12),
            "ema_26": self._ema(closes, 26), "rsi_14": self._rsi(closes),
            "atr_14": self._sma(true_ranges, 14),
            "volatility_20": (sum((value - return_mean) ** 2 for value in recent_returns) / len(recent_returns)) ** 0.5
                if len(recent_returns) >= 20 else None,
            "return_1": closes[-1] / previous - 1 if previous else None,
        }
        return {"symbol": symbol, "interval": interval, "observations": len(candles),
                "as_of": latest["timestamp"], "source": latest.get("source"),
                "data_quality": {"status": "valid", "reason": None},
                "freshness": self._freshness(latest["timestamp"], interval, reference_time),
                "close": closes[-1], "volume": latest["volume"],
                "indicators": indicators}
