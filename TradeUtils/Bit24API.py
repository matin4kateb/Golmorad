"""Bit24 REST API client.

The methods in this module mirror the endpoints documented at
https://docs.bit24.cash/. Trading and withdrawal methods submit real requests;
use them deliberately and keep API credentials outside source control.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from threading import Lock
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://rest.bit24.cash"


class Bit24Error(Exception):
    """Base exception for Bit24 client errors."""


class Bit24HTTPError(Bit24Error):
    """An HTTP or API-level error returned by Bit24."""

    def __init__(self, message: str, status_code: int | None = None,
                 response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class Bit24API:
    """Small standard-library-only client for Bit24's documented REST API.

    Args:
        api_key: Bit24 API token (sent as ``X-BIT24-APIKEY``).
        api_secret: Dedicated signing key from API management. Required only
            for endpoints that document a signature.
        base_url: REST host, normally ``https://rest.bit24.cash``.
        timeout: Per-request timeout in seconds.
        max_requests_per_minute: Client-side safety limit. Bit24 documents
            250 requests/minute for the endpoints covered here.

    The methods return the decoded JSON response envelope (including
    ``success``, ``data`` and ``error``), and raise :class:`Bit24HTTPError`
    for HTTP errors, invalid JSON, or an API envelope with ``success=false``.
    """

    def __init__(self, api_key: str, api_secret: str | None = None,
                 base_url: str = DEFAULT_BASE_URL, timeout: float = 15,
                 max_requests_per_minute: int = 250):
        if not api_key:
            raise ValueError("api_key is required")
        if max_requests_per_minute < 1:
            raise ValueError("max_requests_per_minute must be positive")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_requests_per_minute = max_requests_per_minute
        self._request_times: deque[float] = deque()
        self._rate_lock = Lock()

    @staticmethod
    def create_signature(params: Mapping[str, Any], api_secret: str) -> str:
        """Create Bit24's documented SHA-256 hex signature.

        Parameters are sorted by key and joined as ``key=value&...``. The
        signature itself is excluded. Keep values in their exact submitted
        form (especially decimal numbers) to avoid signature mismatches.
        """
        if not api_secret:
            raise ValueError("api_secret is required to sign this request")
        pairs = (f"{key}={value}" for key, value in sorted(params.items())
                 if key != "signature" and value is not None)
        message = "&".join(pairs)
        # Bit24's documentation gives PHP's hash_hmac('sha256', params, secret)
        # example: the canonical parameter string is the message and the
        # dedicated API key is the HMAC key.
        return hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _signed(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        data = {k: v for k, v in (params or {}).items() if v is not None}
        if not self.api_secret:
            raise ValueError("api_secret is required for this endpoint")
        data["signature"] = self.create_signature(data, self.api_secret)
        return data

    def _wait_for_rate_limit(self) -> None:
        """Apply a rolling one-minute client-side request limit."""
        with self._rate_lock:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()
            if len(self._request_times) >= self.max_requests_per_minute:
                delay = 60 - (now - self._request_times[0])
                if delay > 0:
                    time.sleep(delay)
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= 60:
                    self._request_times.popleft()
            self._request_times.append(time.monotonic())

    def _request(self, method: str, path: str,
                 params: Mapping[str, Any] | None = None,
                 *, authenticated: bool = True) -> dict[str, Any]:
        self._wait_for_rate_limit()
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["X-BIT24-APIKEY"] = self.api_key

        body = None
        if method.upper() == "GET":
            if query:
                url += "?" + urlencode(query, doseq=True)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(query, doseq=True).encode("utf-8")

        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                payload = raw
            raise Bit24HTTPError(self._error_message(payload), exc.code, payload) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Bit24HTTPError(f"Bit24 request failed: {exc}") from exc

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise Bit24HTTPError("Bit24 returned invalid JSON", status, raw) from exc
        if not isinstance(payload, dict):
            raise Bit24HTTPError("Bit24 returned an unexpected response", status, payload)
        if payload.get("success") is False:
            raise Bit24HTTPError(self._error_message(payload),
                                 payload.get("status_code", status), payload)
        return payload

    @staticmethod
    def _error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if payload.get("message"):
                return str(payload["message"])
        return "Bit24 API request failed"

    # Market data ---------------------------------------------------------
    def get_markets(self, page: int = 1, **params: Any) -> dict[str, Any]:
        """List markets (GET /pro/capi/v1/markets)."""
        return self._request("GET", "/pro/capi/v1/markets",
                             {"page": page, **params})

    def get_order_book(self, base_coin: str, quote_coin: str) -> dict[str, Any]:
        """Get a market order book."""
        return self._request("GET", "/pro/capi/v1/markets/order-books",
                             {"base_coin": base_coin, "quote_coin": quote_coin})

    def get_ohlcv(self, **params: Any) -> dict[str, Any]:
        """Get OHLC-V market data; pass the query fields required by Bit24."""
        return self._request("GET", "/pro/capi/v1/markets/ohclv", params)

    # Orders --------------------------------------------------------------
    def submit_order(self, *, base_coin_symbol: str, quote_coin_symbol: str,
                     type: int, category_type: int, amount: Any = None,
                     price: Any = None, quote_coin_amount: Any = None,
                     stop_price: Any = None, trigger_price: Any = None,
                     trailing_price_condition: Any = None,
                     activation_price_condition: Any = None,
                     delta: Any = None,
                     **extra: Any) -> dict[str, Any]:
        """Submit an order (live trading; signed POST).

        ``type``: 0 sell, 1 buy. ``category_type``: 0 limit, 1 market,
        2 stop-loss/take-profit, 3 OCO. Fields depend on order type.
        """
        params = {"base_coin_symbol": base_coin_symbol,
                  "quote_coin_symbol": quote_coin_symbol, "type": type,
                  "category_type": category_type, "amount": amount,
                  "price": price, "quote_coin_amount": quote_coin_amount,
                  "stop_price": stop_price, "trigger_price": trigger_price,
                  "trailing_price_condition": trailing_price_condition,
                  "activation_price_condition": activation_price_condition,
                  "delta": delta,
                  **extra}
        return self._request("POST", "/pro/capi/v1/orders/submit",
                             self._signed(params))

    def cancel_order(self, order_id: int | str | None = None,
                     **extra: Any) -> dict[str, Any]:
        """Cancel one order, or all open orders if ``order_id`` is omitted.

        The docs say cancelling all open orders does not require a signature.
        """
        params = {"order_id": order_id, **extra}
        if order_id is None:
            return self._request("POST", "/pro/capi/v1/orders/cancel", params)
        return self._request("POST", "/pro/capi/v1/orders/cancel",
                             self._signed(params))

    def get_order(self, order_id: int | str) -> dict[str, Any]:
        """Get order details by ID."""
        return self._request("GET", "/pro/capi/v1/orders", {"id": order_id})

    def get_order_history(self, *, is_trade: int, **filters: Any) -> dict[str, Any]:
        """Get paginated order/trade history; ``is_trade`` is required."""
        return self._request("GET", "/pro/capi/v1/orders/get-history",
                             {"is_trade": is_trade, **filters})

    # Wallet and balances -------------------------------------------------
    def get_assets(self, **filters: Any) -> dict[str, Any]:
        """Get wallet assets; supports name, alphabet, without_irt,
        without_zero and page filters."""
        return self._request("GET", "/asset/capi/v1/wallet/assets", filters)

    def get_total_assets(self) -> dict[str, Any]:
        """Get total and available portfolio valuations."""
        return self._request("GET", "/asset/capi/v1/wallet/assets/total")

    def get_wallet_history(self, **filters: Any) -> dict[str, Any]:
        """Get wallet transaction history using documented query filters."""
        return self._request("GET", "/asset/capi/v1/wallet/assets/history", filters)

    # Deposit / withdrawal ------------------------------------------------
    def get_deposit_networks(self, symbol: str) -> dict[str, Any]:
        """Get deposit networks and the assigned deposit address."""
        return self._request("GET", "/lite/capi/v1/deposit/networks",
                             {"symbol": symbol})

    def get_withdraw_networks(self, symbol: str) -> dict[str, Any]:
        """Get withdrawal networks, limits, fees and available balance."""
        return self._request("GET", "/lite/capi/v1/withdraw/networks",
                             {"symbol": symbol})

    def submit_withdrawal(self, *, symbol: str, network_id: int | str,
                          address: str, value: Any, memo: str | None = None,
                          **extra: Any) -> dict[str, Any]:
        """Submit a live withdrawal request (signed POST)."""
        params = {"symbol": symbol, "network_id": network_id,
                  "address": address, "value": value, "memo": memo, **extra}
        return self._request("POST", "/lite/capi/v1/withdraw/submit",
                             self._signed(params))


# Common alternative class name for callers that prefer an explicit client.
Bit24Client = Bit24API
