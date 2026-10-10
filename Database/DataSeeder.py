"""Collectors that keep the Bitcoin technical/fundamental knowledge base fresh."""

from __future__ import annotations

import email.utils
import json
import logging
import math
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from Database.SQLiteUtils import SQLiteUtils, safe_error_detail, utc_now

LOGGER = logging.getLogger(__name__)
FEAR_GREED_URL = "https://api.alternative.me/fng/"
DEFAULT_NEWS_FEEDS = ("https://www.coindesk.com/arc/outboundfeeds/rss/",)
SUPPORTED_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h",
                       "1d", "5d", "1wk", "1mo", "3mo"}
USER_AGENT = "GolmoradKnowledgeBase/1.0 (+local crypto research application)"


def _http_get(url: str, timeout: float = 20) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/xml, text/xml"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:10000]


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class DataSeeder:
    """Fetch and persist independent market, news and sentiment datasets."""

    def __init__(self, database: SQLiteUtils | None = None):
        self.database = database or SQLiteUtils()
        self.symbol = os.getenv("MARKET_SYMBOL", "BTC-USD")
        self.intervals = tuple(i.strip() for i in os.getenv("MARKET_INTERVALS", "1d,1h").split(",") if i.strip())
        self.news_feeds = tuple(i.strip() for i in os.getenv("NEWS_RSS_URLS", ",".join(DEFAULT_NEWS_FEEDS)).split(",") if i.strip())
        try:
            self.timeout = float(os.getenv("DATA_HTTP_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise ValueError("DATA_HTTP_TIMEOUT_SECONDS must be a finite positive number") from exc
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("DATA_HTTP_TIMEOUT_SECONDS must be a finite positive number")
        if not self.symbol.strip():
            raise ValueError("MARKET_SYMBOL must not be empty")
        if not self.intervals or any(interval not in SUPPORTED_INTERVALS for interval in self.intervals):
            raise ValueError("MARKET_INTERVALS must contain supported Yahoo Finance intervals")
        if any(urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname
               for url in self.news_feeds):
            raise ValueError("NEWS_RSS_URLS must contain absolute HTTP or HTTPS URLs")
        self._stop = Event()
        self._thread: Thread | None = None

    def _collect_candles(self, interval: str) -> int:
        run_id = self.database.start_sync(f"candles:{self.symbol}:{interval}", "yfinance")
        try:
            import yfinance as yf

            ticker = yf.Ticker(self.symbol)
            latest = self.database.latest_timestamp(self.symbol, interval)
            if latest:
                latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                # Re-fetch recent data to refresh the current, potentially incomplete candle.
                start = (latest_dt - timedelta(days=3 if interval in {"1d", "1wk", "1mo"} else 2)).date().isoformat()
                frame = ticker.history(start=start, interval=interval, auto_adjust=False, actions=False,
                                       raise_errors=True, timeout=self.timeout)
            else:
                period = "max" if interval in {"1d", "1wk", "1mo"} else "730d"
                frame = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False,
                                       raise_errors=True, timeout=self.timeout)
            fetched_at = utc_now()
            rows = []
            if frame is not None and not frame.empty:
                for stamp, item in frame.iterrows():
                    def number(key: str, default: float | None = None) -> float | None:
                        value = item.get(key, default)
                        try:
                            result = float(value)
                            return result if result == result else default
                        except (TypeError, ValueError):
                            return default
                    opened, high, low, close = (number(name) for name in ("Open", "High", "Low", "Close"))
                    if None in (opened, high, low, close):
                        continue
                    stamp_dt = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
                    if stamp_dt.tzinfo is None:
                        stamp_dt = stamp_dt.replace(tzinfo=timezone.utc)
                    timestamp = stamp_dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                    rows.append({"symbol": self.symbol, "interval": interval, "timestamp": timestamp,
                                 "open": opened, "high": high, "low": low, "close": close,
                                 "adjusted_close": number("Adj Close"), "volume": number("Volume", 0) or 0,
                                 "source": "yfinance", "fetched_at": fetched_at})
            count = self.database.upsert_candles(rows)
            self.database.finish_sync(run_id, status="success", rows_inserted=count)
            LOGGER.info("Stored %d %s %s candles", count, self.symbol, interval)
            return count
        except Exception as exc:
            safe_error = safe_error_detail(exc)
            self.database.finish_sync(run_id, status="failed", error=safe_error)
            LOGGER.error("Candle update failed for %s %s (%s)", self.symbol, interval, safe_error)
            return 0

    def _collect_fear_greed(self) -> int:
        run_id = self.database.start_sync("fear_greed", "alternative.me")
        try:
            # Backfill the provider's history once; subsequent cycles only need
            # a short overlap to catch today's observation and remain lightweight.
            limit = 0 if not self.database.get_sentiment(limit=1) else 2
            payload = json.loads(_http_get(f"{FEAR_GREED_URL}?limit={limit}&format=json", self.timeout))
            records = payload.get("data", [])
            fetched_at = utc_now()
            rows = []
            for item in records:
                timestamp = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                rows.append({"provider": "alternative.me", "index_name": "crypto_fear_greed",
                             "observed_at": timestamp, "value": int(item["value"]),
                             "classification": str(item["value_classification"]), "source_url": FEAR_GREED_URL,
                             "fetched_at": fetched_at, "raw": item})
            count = self.database.upsert_sentiment(rows)
            self.database.finish_sync(run_id, status="success", rows_inserted=count)
            LOGGER.info("Stored %d fear-and-greed observations", count)
            return count
        except Exception as exc:
            safe_error = safe_error_detail(exc)
            self.database.finish_sync(run_id, status="failed", error=safe_error)
            LOGGER.error("Fear-and-greed update failed (%s)", safe_error)
            return 0

    def _collect_news_feed(self, feed_url: str) -> int:
        host = urlparse(feed_url).hostname or "rss"
        host = host.lower()
        run_id = self.database.start_sync(f"news:{host}", host)
        try:
            root = ET.fromstring(_http_get(feed_url, self.timeout))
            fetched_at = utc_now()
            rows = []
            for item in root.iter():
                tag = item.tag.rsplit("}", 1)[-1].lower()
                if tag not in {"item", "entry"}:
                    continue
                fields: dict[str, str] = {}
                for child in item:
                    key = child.tag.rsplit("}", 1)[-1].lower()
                    value = (child.attrib.get("href") or "".join(child.itertext())) if key == "link" else "".join(child.itertext())
                    if key not in fields and value:
                        fields[key] = value.strip()
                title = _clean_text(fields.get("title"))
                summary = _clean_text(fields.get("description") or fields.get("summary") or fields.get("content"))
                searchable = f"{title or ''} {summary or ''}".lower()
                if not title or not ("bitcoin" in searchable or "btc" in searchable):
                    continue
                url = fields.get("link")
                if not url:
                    continue
                rows.append({"source": host, "external_id": fields.get("guid") or fields.get("id"),
                             "url": url, "title": title, "summary": summary,
                             "published_at": _parse_date(fields.get("pubdate") or fields.get("published") or fields.get("updated")),
                             "fetched_at": fetched_at, "raw": fields})
            count = self.database.upsert_news(rows)
            self.database.finish_sync(run_id, status="success", rows_inserted=count)
            LOGGER.info("Stored %d Bitcoin news items from %s", count, host)
            return count
        except Exception as exc:
            safe_error = safe_error_detail(exc)
            self.database.finish_sync(run_id, status="failed", error=safe_error)
            LOGGER.error("News update failed for %s (%s)", host, safe_error)
            return 0

    def run_once(self) -> dict[str, int]:
        """Initialize storage, then attempt every configured dataset."""
        self.database.initialize()
        totals = {f"candles_{interval}": self._collect_candles(interval) for interval in self.intervals}
        totals["fear_greed"] = self._collect_fear_greed()
        totals["news"] = sum(self._collect_news_feed(url) for url in self.news_feeds)
        return totals

    def start(self, interval_seconds: int = 900, *, run_immediately: bool = True) -> None:
        """Start a single background refresh loop; safe to call only once."""
        if interval_seconds < 60:
            raise ValueError("interval_seconds must be at least 60")
        if self._thread and self._thread.is_alive():
            return
        self.database.initialize()
        self._stop.clear()

        def run_cycle() -> None:
            try:
                self.run_once()
            except Exception as exc:
                # Keep the scheduler alive after database or orchestration
                # failures and retry only after the configured wait.
                LOGGER.error("Data collection cycle failed (%s)", safe_error_detail(exc))

        def loop() -> None:
            if run_immediately:
                run_cycle()
            while not self._stop.wait(interval_seconds):
                run_cycle()

        self._thread = Thread(target=loop, name="golmorad-data-seeder", daemon=False)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError("Data collection is still running; shutdown did not complete cleanly")
