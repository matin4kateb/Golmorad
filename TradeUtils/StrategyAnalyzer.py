"""Transparent baseline strategy built from the stored technical indicators."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from TradeUtils.TechnicalAnalyzer import TechnicalAnalyzer


class StrategyAnalyzer:
    """Produce a conservative, explainable spot-market analysis.

    The baseline is a ruleset, not a trained or backtested machine-learning
    model. It only proposes a long setup when trend, momentum, and volatility
    data are all available.
    """

    def __init__(self, technical_analyzer: TechnicalAnalyzer | None = None):
        self.technical_analyzer = technical_analyzer or TechnicalAnalyzer()

    def analyze(self, symbol: str = "BTC-USD", interval: str = "1d",
                limit: int = 500, *, candles: list[dict[str, Any]] | None = None,
                reference_time: datetime | None = None,
                parameters: dict[str, float] | None = None) -> dict[str, Any]:
        parameters = parameters or {}
        stop_atr = float(parameters.get("stop_atr_multiple", 2.0))
        reward_risk = float(parameters.get("reward_risk_ratio", 2.0))
        rsi_lower = float(parameters.get("rsi_lower", 30.0))
        rsi_upper = float(parameters.get("rsi_upper", 70.0))
        if (not all(math.isfinite(value) for value in
                    (stop_atr, reward_risk, rsi_lower, rsi_upper))
                or stop_atr <= 0 or reward_risk <= 0
                or not 0 <= rsi_lower < rsi_upper <= 100):
            raise ValueError("Strategy parameters are outside valid bounds")
        technical = self.technical_analyzer.analyze(symbol, interval, limit, candles=candles,
                                                    reference_time=reference_time)
        provenance = {
            "as_of": technical.get("as_of"),
            "source": technical.get("source"),
            "freshness": technical.get("freshness", {"status": "unknown"}),
        }
        quality = technical.get("data_quality", {"status": "unknown"})
        if quality.get("status") == "invalid":
            return {
                "symbol": symbol, "interval": interval, "signal": "invalid_data",
                "reason": quality.get("reason") or "Stored candle data failed validation.",
                **provenance, "data_quality": quality,
                "technical": technical, "trade_plan": None,
            }
        indicators = technical["indicators"]
        freshness = provenance["freshness"]
        if technical.get("observations", 0) > 0 and freshness.get("status") != "fresh":
            stale = freshness.get("status") == "stale"
            return {
                "symbol": symbol, "interval": interval,
                "signal": "stale_data" if stale else "freshness_unknown",
                "reason": ("The latest candle is older than the allowed freshness window."
                           if stale else "Candle freshness could not be established."),
                **provenance, "data_quality": quality,
                "technical": technical, "trade_plan": None,
            }
        required = (technical.get("close"), indicators.get("ema_12"),
                    indicators.get("ema_26"), indicators.get("sma_50"),
                    indicators.get("rsi_14"), indicators.get("atr_14"))
        if technical["observations"] < 50 or any(value is None for value in required):
            return {
                "symbol": symbol, "interval": interval,
                "signal": "insufficient_data",
                "reason": "At least 50 usable candles are needed for this baseline.",
                **provenance,
                "data_quality": quality,
                "technical": technical, "trade_plan": None,
            }

        close = float(technical["close"])
        ema_fast, ema_slow = float(indicators["ema_12"]), float(indicators["ema_26"])
        sma_trend, rsi, atr = (float(indicators[key]) for key in
                               ("sma_50", "rsi_14", "atr_14"))
        if atr <= 0:
            return {
                "symbol": symbol, "interval": interval, "signal": "hold",
                "reason": "ATR is zero or invalid, so a risk-based stop cannot be set.",
                **provenance,
                "technical": technical, "trade_plan": None,
            }

        trend_up = close > sma_trend and ema_fast > ema_slow
        trend_down = close < sma_trend and ema_fast < ema_slow
        if trend_up and rsi_lower <= rsi < rsi_upper:
            stop = close - stop_atr * atr
            if stop <= 0:
                signal, reason, plan = "hold", "The ATR stop is not a valid positive price.", None
            else:
                signal, reason = "buy", "Uptrend confirmed by EMA/SMA; RSI is below the overbought threshold."
                plan = {
                    "side": "long", "entry_price": close, "stop_price": stop,
                    "target_price": close + reward_risk * (close - stop),
                    "risk_reward_ratio": reward_risk,
                }
        elif trend_down:
            signal, reason, plan = "avoid", "Trend filters are bearish; this spot baseline does not open short positions.", None
        elif rsi >= 70:
            signal, reason, plan = "hold", "Momentum is overbought; wait for a clearer setup.", None
        elif rsi < 30:
            signal, reason, plan = "hold", "Momentum is oversold; wait for confirmation instead of catching a falling market.", None
        else:
            signal, reason, plan = "hold", "Trend filters do not agree on a long setup.", None

        return {
            "symbol": symbol, "interval": interval, "signal": signal,
            "reason": reason, **provenance,
            "data_quality": quality,
            "strategy_parameters": {"stop_atr_multiple": stop_atr,
                                    "reward_risk_ratio": reward_risk,
                                    "rsi_lower": rsi_lower, "rsi_upper": rsi_upper},
            "technical": technical, "trade_plan": plan,
        }
