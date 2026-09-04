"""Small, read-only E*TRADE OAuth 1.0a client for Streamlit.

This module deliberately has no order-preview or order-placement methods. The
app is an OTOCO simulator and cannot transmit a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote

from requests_oauthlib import OAuth1Session


LIVE_BASE = "https://api.etrade.com"
SANDBOX_BASE = "https://apisb.etrade.com"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"


class ETradeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequestCredentials:
    oauth_token: str
    oauth_token_secret: str
    authorization_url: str


def _base(environment: str) -> str:
    return LIVE_BASE if str(environment).lower() == "live" else SANDBOX_BASE


def _json_response(response) -> dict[str, Any]:
    if response.status_code == 401:
        raise ETradeError(
            "E*TRADE rejected the token. Renew it if it is still the same day, "
            "or reconnect and enter a new verification code."
        )
    try:
        response.raise_for_status()
    except Exception as exc:
        message = response.text.strip()[:500]
        raise ETradeError(f"E*TRADE returned HTTP {response.status_code}: {message}") from exc
    try:
        return response.json()
    except Exception as exc:
        raise ETradeError("E*TRADE returned a response that was not JSON.") from exc


def begin_authorization(
    consumer_key: str,
    consumer_secret: str,
    environment: str = "live",
) -> RequestCredentials:
    base = _base(environment)
    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        callback_uri="oob",
    )
    try:
        token = oauth.fetch_request_token(f"{base}/oauth/request_token")
    except Exception as exc:
        raise ETradeError(f"Could not start E*TRADE authorization: {exc}") from exc

    request_key = token.get("oauth_token")
    request_secret = token.get("oauth_token_secret")
    if not request_key or not request_secret:
        raise ETradeError("E*TRADE did not return a complete request token.")

    url = f"{AUTHORIZE_URL}?key={quote(consumer_key)}&token={quote(request_key)}"
    return RequestCredentials(request_key, request_secret, url)


def complete_authorization(
    consumer_key: str,
    consumer_secret: str,
    request_token: str,
    request_token_secret: str,
    verifier: str,
    environment: str = "live",
) -> dict[str, str]:
    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=request_token,
        resource_owner_secret=request_token_secret,
        verifier=str(verifier).strip(),
    )
    try:
        token = oauth.fetch_access_token(f"{_base(environment)}/oauth/access_token")
    except Exception as exc:
        raise ETradeError(f"E*TRADE could not verify that code: {exc}") from exc

    access_key = token.get("oauth_token")
    access_secret = token.get("oauth_token_secret")
    if not access_key or not access_secret:
        raise ETradeError("E*TRADE did not return a complete access token.")
    return {"oauth_token": access_key, "oauth_token_secret": access_secret}


class ETradeClient:
    """Authenticated E*TRADE client exposing read-only account and market calls."""

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        oauth_token: str,
        oauth_token_secret: str,
        environment: str = "live",
    ) -> None:
        self.base = _base(environment)
        self.session = OAuth1Session(
            consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_token_secret,
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base}{path}",
            params=params or {},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        return _json_response(response)

    def renew(self) -> None:
        response = self.session.get(
            f"{self.base}/oauth/renew_access_token",
            headers={"Accept": "text/plain"},
            timeout=30,
        )
        if not response.ok:
            _json_response(response)

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._get("/v1/accounts/list")
        accounts = _find_key(data, "account")
        return _as_list(accounts)

    def get_balance(self, account_id_key: str) -> dict[str, Any]:
        return self._get(
            f"/v1/accounts/{quote(account_id_key, safe='')}/balance",
            {"instType": "BROKERAGE", "realTimeNAV": "true"},
        )

    def get_portfolio(self, account_id_key: str) -> list[dict[str, Any]]:
        all_positions: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                f"/v1/accounts/{quote(account_id_key, safe='')}/portfolio",
                {
                    "count": 50,
                    "pageNumber": page,
                    "totalsRequired": "true",
                    "view": "COMPLETE",
                },
            )
            positions = _as_list(_find_key(data, "position"))
            all_positions.extend(p for p in positions if isinstance(p, dict))
            total_pages = _coerce_int(_find_key(data, "totalNoOfPages"), default=1)
            if page >= total_pages:
                break
            page += 1
        return all_positions

    def get_quote(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ETradeError("Enter a symbol first.")
        return self._get(
            f"/v1/market/quote/{quote(symbol, safe='')}",
            {"detailFlag": "ALL"},
        )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def _find_key(value: Any, wanted: str) -> Any:
    wanted = wanted.casefold()
    for node in walk_dicts(value):
        for key, child in node.items():
            if str(key).casefold() == wanted:
                return child
    return None


def find_number(value: Any, *keys: str) -> float | None:
    """Find the first usable numeric value under any case-insensitive key."""
    wanted = {key.casefold() for key in keys}
    for node in walk_dicts(value):
        for key, child in node.items():
            if str(key).casefold() in wanted:
                try:
                    return float(child)
                except (TypeError, ValueError):
                    continue
    return None


def quote_summary(payload: dict[str, Any]) -> dict[str, Any]:
    quote_data = _as_list(_find_key(payload, "quoteData"))
    record = quote_data[0] if quote_data and isinstance(quote_data[0], dict) else payload

    last = find_number(record, "lastTrade", "lastPrice")
    bid = find_number(record, "bid")
    ask = find_number(record, "ask")
    if last is None and bid is not None and ask is not None:
        last = (bid + ask) / 2.0
    if last is None:
        raise ETradeError("The quote response did not contain a usable last price.")

    symbol = _find_key(record, "symbol") or ""
    description = _find_key(record, "companyName") or _find_key(record, "securityName") or ""
    return {
        "symbol": str(symbol),
        "description": str(description),
        "last": last,
        "bid": bid,
        "ask": ask,
        "change": find_number(record, "changeClose", "change"),
        "change_pct": find_number(record, "changeClosePercentage", "changePct"),
        "quote_status": _find_key(record, "dateTimeUTC") or _find_key(record, "dateTime"),
    }


def total_account_value(payload: dict[str, Any]) -> float | None:
    return find_number(payload, "totalAccountValue", "accountBalance", "netAccountValue")


def normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    product = _find_key(position, "Product")
    product = product if isinstance(product, dict) else {}
    symbol = _find_key(product, "symbol") or _find_key(position, "symbol") or ""
    security_type = _find_key(product, "securityType") or _find_key(position, "securityType") or ""
    quantity = find_number(position, "quantity") or 0.0
    price_paid = find_number(position, "pricePaid", "costPerShare")
    last_price = find_number(position, "lastTrade", "price", "marketPrice")
    market_value = find_number(position, "marketValue")
    total_cost = find_number(position, "totalCost")
    total_gain = find_number(position, "totalGain")
    total_gain_pct = find_number(position, "totalGainPct")
    pct_portfolio = find_number(position, "pctOfPortfolio")
    return {
        "Symbol": str(symbol),
        "Type": str(security_type),
        "Quantity": quantity,
        "Last": last_price,
        "Price Paid": price_paid,
        "Market Value": market_value,
        "Total Cost": total_cost,
        "Gain/Loss": total_gain,
        "Gain/Loss %": total_gain_pct,
        "% Portfolio": pct_portfolio,
    }

