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

    def get_option_expirations(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ETradeError("Enter a symbol first.")
        return self._get(
            "/v1/market/optionexpiredate",
            {"symbol": symbol, "expiryType": "ALL"},
        )

    def get_option_chain(
        self,
        symbol: str,
        expiry_year: int,
        expiry_month: int,
        expiry_day: int,
        no_of_strikes: int = 100,
    ) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ETradeError("Enter a symbol first.")
        return self._get(
            "/v1/market/optionchains",
            {
                "symbol": symbol,
                "expiryYear": int(expiry_year),
                "expiryMonth": int(expiry_month),
                "expiryDay": int(expiry_day),
                "chainType": "CALLPUT",
                "skipAdjusted": "true",
                "optionCategory": "STANDARD",
                "priceType": "ALL",
                "noOfStrikes": int(no_of_strikes),
            },
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
    """Find the first usable numeric value, honoring the requested key priority."""
    for key in keys:
        child = _find_key(value, key)
        try:
            return float(child)
        except (TypeError, ValueError):
            continue
    return None


def option_expiration_dates(payload: dict[str, Any]) -> list[tuple[int, int, int]]:
    """Return option expirations as sorted (year, month, day) tuples."""
    raw = _find_key(payload, "ExpirationDate")
    dates: list[tuple[int, int, int]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        try:
            dates.append((
                int(item.get("year")),
                int(item.get("month")),
                int(item.get("day")),
            ))
        except (TypeError, ValueError):
            continue
    return sorted(set(dates))


def gamma_wall_summary(payload: dict[str, Any], spot: float) -> dict[str, Any]:
    """Estimate call/put gamma walls from one E*TRADE option-chain expiry.

    Exposure is approximated as gamma × open interest × 100 × spot² × 1%.
    Puts are signed negative for display. If gamma is unavailable for the
    entire side, open interest is used only as a fallback ranking measure.
    """
    try:
        spot = float(spot)
    except (TypeError, ValueError) as exc:
        raise ETradeError("A usable underlying price is required for gamma walls.") from exc
    if spot <= 0:
        raise ETradeError("A positive underlying price is required for gamma walls.")

    pairs = _as_list(_find_key(payload, "OptionPair"))
    call_gex: dict[float, float] = {}
    put_gex: dict[float, float] = {}
    call_oi: dict[float, float] = {}
    put_oi: dict[float, float] = {}

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        for side, signed_map, oi_map, sign in (
            ("Call", call_gex, call_oi, 1.0),
            ("Put", put_gex, put_oi, -1.0),
        ):
            option = pair.get(side) or pair.get(side.lower())
            if not isinstance(option, dict):
                continue
            strike = find_number(option, "strikePrice", "strike")
            open_interest = find_number(option, "openInterest")
            greeks = (
                option.get("OptionGreeks")
                or option.get("optionGreeks")
                or option.get("optionGreek")
                or {}
            )
            gamma = find_number(greeks, "gamma") if isinstance(greeks, dict) else None
            if strike is None or open_interest is None or open_interest < 0:
                continue
            strike = float(strike)
            oi_map[strike] = oi_map.get(strike, 0.0) + float(open_interest)
            if gamma is None or gamma < 0:
                continue
            exposure = float(gamma) * float(open_interest) * 100.0 * spot * spot * 0.01
            signed_map[strike] = signed_map.get(strike, 0.0) + sign * exposure

    method = "GAMMA×OI"
    if call_gex:
        call_wall, call_value = max(call_gex.items(), key=lambda item: item[1])
    elif call_oi:
        method = "OPEN INTEREST FALLBACK"
        call_wall, call_value = max(call_oi.items(), key=lambda item: item[1])
    else:
        call_wall = call_value = None

    if put_gex:
        put_wall, put_value = min(put_gex.items(), key=lambda item: item[1])
    elif put_oi:
        method = "OPEN INTEREST FALLBACK"
        put_wall, put_value = max(put_oi.items(), key=lambda item: item[1])
        put_value = -float(put_value)
    else:
        put_wall = put_value = None

    if call_wall is None or put_wall is None:
        raise ETradeError(
            "E*TRADE option chain did not contain enough open-interest/Greek data "
            "to estimate both gamma walls."
        )

    return {
        "call_wall": float(call_wall),
        "put_wall": float(put_wall),
        "call_exposure": float(call_value),
        "put_exposure": float(put_value),
        "method": method,
        "contracts_seen": len(pairs),
    }


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
    return find_number(payload, "totalAccountValue", "netAccountValue")


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
    day_gain = find_number(position, "daysGain")
    day_gain_pct = find_number(position, "daysGainPct")
    total_gain = find_number(position, "totalGain")
    total_gain_pct = find_number(position, "totalGainPct")
    pct_portfolio = find_number(position, "pctOfPortfolio")
    week_52_high = find_number(position, "week52High")
    week_52_low = find_number(position, "week52Low")
    from_52_week_high = None
    if last_price is not None and week_52_high not in (None, 0):
        from_52_week_high = (last_price / week_52_high - 1.0) * 100.0
    return {
        "Symbol": str(symbol),
        "Type": str(security_type),
        "Quantity": quantity,
        "Last": last_price,
        "Price Paid": price_paid,
        "Market Value": market_value,
        "Total Cost": total_cost,
        "Day Gain/Loss": day_gain,
        "Day Gain/Loss %": day_gain_pct,
        "Gain/Loss": total_gain,
        "Gain/Loss %": total_gain_pct,
        "% Portfolio": pct_portfolio,
        "52W High": week_52_high,
        "52W Low": week_52_low,
        "% From 52W High": from_52_week_high,
    }
