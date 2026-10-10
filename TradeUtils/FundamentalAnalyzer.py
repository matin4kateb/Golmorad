"""Fundamental context from persisted Bitcoin news and sentiment observations."""

from __future__ import annotations

from typing import Any

from Database.SQLiteUtils import SQLiteUtils


class FundamentalAnalyzer:
    """Expose recent source-linked news and market-wide sentiment to strategies."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()

    def analyze(self, *, news_limit: int = 30, sentiment_limit: int = 30) -> dict[str, Any]:
        news = self.database.get_news(news_limit)
        sentiment = self.database.get_sentiment(limit=sentiment_limit)
        latest = sentiment[-1] if sentiment else None
        values = [int(row["value"]) for row in sentiment]
        return {
            "news": news,
            "news_count": len(news),
            "sentiment": latest,
            "sentiment_history": sentiment,
            "sentiment_average": sum(values) / len(values) if values else None,
        }
