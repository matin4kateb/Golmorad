"""Technical indicators computed from persisted OHLCV candles."""

from __future__ import annotations

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

    def analyze(self, symbol: str = "BTC-USD", interval: str = "1d", limit: int = 500) -> dict[str, Any]:
        candles = self.database.get_candles(symbol, interval, limit)
        if not candles:
            return {"symbol": symbol, "interval": interval, "observations": 0, "indicators": {}}
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
                "as_of": latest["timestamp"], "close": closes[-1], "volume": latest["volume"],
                "indicators": indicators}
