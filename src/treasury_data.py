import json
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup


WSJ_TREASURY_URL = "https://www.wsj.com/market-data/bonds/treasuries"
TREASURY_CURVE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/TextView?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": WSJ_TREASURY_URL,
}


def _num(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.lower() in {"", "na", "n/a", "none", "null", "unch", "unch."}:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _date(value):
    if value is None:
        return pd.NaT
    if isinstance(value, (int, float)):
        try:
            if abs(value) > 10_000_000_000:
                return pd.to_datetime(value, unit="ms", errors="coerce")
            if abs(value) > 1_000_000_000:
                return pd.to_datetime(value, unit="s", errors="coerce")
        except Exception:
            pass
    return pd.to_datetime(value, errors="coerce")


def _clean_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def _security_type(coupon, maturity, as_of=None, explicit=None):
    if explicit:
        text = str(explicit).lower()
        if "bill" in text:
            return "Treasury Bill"
        if "note" in text:
            return "Treasury Note"
        if "bond" in text:
            return "Treasury Bond"

    as_of = pd.Timestamp(as_of or pd.Timestamp.today()).normalize()
    maturity = pd.to_datetime(maturity, errors="coerce")
    remaining_days = (maturity - as_of).days if pd.notna(maturity) else None

    if (coupon is None or abs(coupon) < 1e-12) and remaining_days is not None and remaining_days <= 400:
        return "Treasury Bill"
    return "Treasury Note/Bond"


def _dedupe(rows):
    if not rows:
        return pd.DataFrame(
            columns=[
                "Security Type", "Maturity", "Coupon (%)", "Bid", "Asked",
                "Asked Yield (%)", "Source"
            ]
        )

    df = pd.DataFrame(rows)
    df["Maturity"] = pd.to_datetime(df["Maturity"], errors="coerce")
    for col in ["Coupon (%)", "Bid", "Asked", "Asked Yield (%)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Maturity", "Asked Yield (%)"]).copy()
    df = df[df["Asked Yield (%)"].between(-5, 25, inclusive="both")].copy()

    subset = ["Maturity", "Coupon (%)", "Asked Yield (%)"]
    return (
        df.sort_values(["Maturity", "Asked Yield (%)"])
        .drop_duplicates(subset=subset, keep="last")
        .reset_index(drop=True)
    )


def _flatten_dict(obj, prefix=""):
    out = {}
    if not isinstance(obj, dict):
        return out

    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_dict(value, path))
        elif isinstance(value, list):
            if value and not isinstance(value[0], (dict, list)):
                out[path] = value[0]
        else:
            out[path] = value
    return out


def _pick(flat, includes, prefer=()):
    items = list(flat.items())
    for pref in prefer:
        for key, value in items:
            low = key.lower()
            if all(term in low for term in includes) and pref in low:
                return value
    for key, value in items:
        low = key.lower()
        if all(term in low for term in includes):
            return value
    return None


def _parse_wsj_instruments(instruments, explicit_type, as_of):
    rows = []

    for item in instruments or []:
        if not isinstance(item, dict):
            continue

        flat = _flatten_dict(item)

        maturity_value = _pick(flat, ("matur",), prefer=("date", "formatted"))
        yield_value = _pick(flat, ("yield",), prefer=("asked", "ask", "formatted"))
        coupon_value = _pick(flat, ("coupon",), prefer=("rate", "formatted"))
        bid_value = _pick(flat, ("bid",), prefer=("price", "formatted"))
        asked_value = _pick(flat, ("ask",), prefer=("price", "formatted"))

        maturity = _date(maturity_value)
        yld = _num(yield_value)
        coupon = _num(coupon_value)

        if pd.isna(maturity) or yld is None or not (-5 <= yld <= 25):
            continue

        rows.append(
            {
                "Security Type": _security_type(
                    coupon, maturity, as_of, explicit=explicit_type
                ),
                "Maturity": maturity,
                "Coupon (%)": coupon,
                "Bid": _num(bid_value),
                "Asked": _num(asked_value),
                "Asked Yield (%)": yld,
                "Source": "WSJ U.S. Treasury Quotes",
            }
        )

    return rows


def _load_wsj_ajax(session, timeout):
    """
    WSJ historically exposes the two Treasury tabs through the same public
    mdc_treasury JSON route used by the webpage:
      id={"treasury":"BILLS"}
      id={"treasury":"NOTES_AND_BONDS"}
    """
    groups = [
        ("BILLS", "Treasury Bill"),
        ("NOTES_AND_BONDS", "Treasury Note/Bond"),
    ]
    all_rows = []
    errors = []
    as_of = pd.Timestamp.today().normalize()

    for group, explicit_type in groups:
        try:
            response = session.get(
                WSJ_TREASURY_URL,
                params={
                    "id": json.dumps({"treasury": group}, separators=(",", ":")),
                    "type": "mdc_treasury",
                },
                headers=DEFAULT_HEADERS,
                timeout=timeout,
            )

            if response.status_code in {401, 403, 429}:
                raise RuntimeError(
                    f"WSJ blocked {group} request (HTTP {response.status_code})."
                )
            response.raise_for_status()

            payload = response.json()
            instruments = (
                payload.get("data", {}).get("instruments", [])
                if isinstance(payload, dict)
                else []
            )
            rows = _parse_wsj_instruments(instruments, explicit_type, as_of)
            if not rows:
                raise RuntimeError(
                    f"WSJ {group} endpoint returned no parseable Treasury rows."
                )
            all_rows.extend(rows)

        except Exception as exc:
            errors.append(f"{group}: {exc}")

    types = set(r["Security Type"] for r in all_rows)
    has_bills = any("Bill" in x for x in types)
    has_notes_bonds = any("Note" in x or "Bond" in x for x in types)

    if not (has_bills and has_notes_bonds):
        raise RuntimeError(
            "WSJ JSON Treasury feed was incomplete. "
            + (" | ".join(errors) if errors else "")
        )

    return _dedupe(all_rows), errors


def _extract_html_tables(soup, as_of):
    rows = []

    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue

        headers = [
            _clean_header(x.get_text(" ", strip=True))
            for x in trs[0].find_all(["th", "td"])
        ]
        if not headers or not any("MATURITY" in h for h in headers):
            continue
        if not any("YIELD" in h for h in headers):
            continue

        def idx_contains(*terms):
            for i, h in enumerate(headers):
                if all(term in h for term in terms):
                    return i
            return None

        maturity_i = idx_contains("MATURITY")
        coupon_i = idx_contains("COUPON")
        bid_i = idx_contains("BID")
        asked_i = idx_contains("ASK")
        yield_i = idx_contains("YIELD")

        if maturity_i is None or yield_i is None:
            continue

        context = " ".join(
            x.get_text(" ", strip=True)
            for x in list(table.previous_siblings)[:4]
            if getattr(x, "get_text", None)
        )
        explicit_type = (
            "Treasury Bill" if "Treasury Bill" in context else "Treasury Note/Bond"
        )

        for tr in trs[1:]:
            cells = [
                x.get_text(" ", strip=True)
                for x in tr.find_all(["th", "td"])
            ]
            if len(cells) <= max(maturity_i, yield_i):
                continue

            maturity = _date(cells[maturity_i])
            yld = _num(cells[yield_i])
            if pd.isna(maturity) or yld is None:
                continue

            coupon = (
                _num(cells[coupon_i])
                if coupon_i is not None and coupon_i < len(cells)
                else None
            )
            bid = (
                _num(cells[bid_i])
                if bid_i is not None and bid_i < len(cells)
                else None
            )
            asked = (
                _num(cells[asked_i])
                if asked_i is not None and asked_i < len(cells)
                else None
            )

            rows.append(
                {
                    "Security Type": _security_type(
                        coupon, maturity, as_of, explicit_type
                    ),
                    "Maturity": maturity,
                    "Coupon (%)": coupon,
                    "Bid": bid,
                    "Asked": asked,
                    "Asked Yield (%)": yld,
                    "Source": "WSJ U.S. Treasury Quotes",
                }
            )

    return rows


def _walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_json(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_json(value)


def _extract_json_rows(soup, as_of):
    rows = []

    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=False)
        if not raw:
            continue

        if "window.__STATE__" in raw:
            candidate = raw.replace("window.__STATE__ =", "", 1).strip()
            if candidate.endswith(";"):
                candidate = candidate[:-1]
            try:
                payload = json.loads(candidate)
                for item in _walk_json(payload):
                    if not isinstance(item, dict):
                        continue
                    flat = _flatten_dict(item)
                    maturity = _date(
                        _pick(flat, ("matur",), prefer=("date", "formatted"))
                    )
                    yld = _num(
                        _pick(flat, ("yield",), prefer=("asked", "ask", "formatted"))
                    )
                    if pd.isna(maturity) or yld is None:
                        continue
                    coupon = _num(
                        _pick(flat, ("coupon",), prefer=("rate", "formatted"))
                    )
                    rows.append(
                        {
                            "Security Type": _security_type(coupon, maturity, as_of),
                            "Maturity": maturity,
                            "Coupon (%)": coupon,
                            "Bid": _num(
                                _pick(flat, ("bid",), prefer=("price", "formatted"))
                            ),
                            "Asked": _num(
                                _pick(flat, ("ask",), prefer=("price", "formatted"))
                            ),
                            "Asked Yield (%)": yld,
                            "Source": "WSJ U.S. Treasury Quotes",
                        }
                    )
            except Exception:
                pass

    return rows


def load_wsj_treasury_quotes(timeout=25):
    """
    WSJ-first loader.

    1) Try the public JSON route used by the Treasury Bills and Notes/Bonds tabs.
    2) Try embedded page state / HTML.
    3) Never bypass authentication, CAPTCHAs, access controls, or paywalls.
    """
    session = requests.Session()

    try:
        ajax_df, ajax_errors = _load_wsj_ajax(session, timeout)
        if not ajax_df.empty:
            return ajax_df, {
                "source": "WSJ U.S. Treasury Quotes",
                "source_url": WSJ_TREASURY_URL,
                "as_of": pd.Timestamp.today().strftime("%Y-%m-%d"),
                "fallback": False,
                "note": (
                    "WSJ Treasury Bills + Notes/Bonds public mdc_treasury feed; "
                    "asked yield used for comparison."
                ),
                "warnings": ajax_errors,
            }
    except Exception as ajax_exc:
        ajax_error = str(ajax_exc)

    response = session.get(
        WSJ_TREASURY_URL,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )

    if response.status_code in {401, 403, 429}:
        raise RuntimeError(
            f"WSJ blocked automated access (HTTP {response.status_code})."
        )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    date_match = re.search(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        soup.get_text(" ", strip=True),
    )
    as_of = (
        _date(date_match.group(2))
        if date_match
        else pd.Timestamp.today().normalize()
    )

    rows = []
    rows.extend(_extract_html_tables(soup, as_of))
    rows.extend(_extract_json_rows(soup, as_of))

    df = _dedupe(rows)
    if df.empty:
        raise RuntimeError(
            "WSJ Treasury feed/page did not expose a complete parseable quote set. "
            f"JSON attempt: {ajax_error}"
        )

    return df, {
        "source": "WSJ U.S. Treasury Quotes",
        "source_url": WSJ_TREASURY_URL,
        "as_of": pd.Timestamp(as_of).strftime("%Y-%m-%d"),
        "fallback": False,
        "note": "Representative WSJ Treasury quotations; asked yield used for comparison.",
        "warnings": [ajax_error] if ajax_error else [],
    }


def _tenor_months(header):
    h = _clean_header(header)
    mapping = {
        "1 MO": 1,
        "1.5 MONTH": 1.5,
        "2 MO": 2,
        "3 MO": 3,
        "4 MO": 4,
        "6 MO": 6,
        "1 YR": 12,
        "2 YR": 24,
        "3 YR": 36,
        "5 YR": 60,
        "7 YR": 84,
        "10 YR": 120,
        "20 YR": 240,
        "30 YR": 360,
    }
    return mapping.get(h)


def load_treasury_curve_fallback(timeout=25):
    """
    Official U.S. Treasury daily par yield curve fallback.
    These are benchmark tenors, not individual secondary-market CUSIP quotes.
    """
    today = pd.Timestamp.today().normalize()
    url = TREASURY_CURVE_URL.format(year=today.year)
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    chosen_table = None
    headers = None

    for table in soup.find_all("table"):
        first = table.find("tr")
        if not first:
            continue
        hs = [
            _clean_header(x.get_text(" ", strip=True))
            for x in first.find_all(["th", "td"])
        ]
        if (
            hs
            and hs[0] == "DATE"
            and any(_tenor_months(h) is not None for h in hs[1:])
        ):
            chosen_table = table
            headers = hs
            break

    if chosen_table is None:
        raise RuntimeError("Official Treasury yield-curve table was not found.")

    observations = []
    for tr in chosen_table.find_all("tr")[1:]:
        cells = [
            x.get_text(" ", strip=True)
            for x in tr.find_all(["th", "td"])
        ]
        if len(cells) < 2:
            continue
        d = _date(cells[0])
        if pd.isna(d):
            continue
        observations.append((d, cells))

    if not observations:
        raise RuntimeError(
            "Official Treasury yield-curve page returned no observations."
        )

    observations = [
        x for x in observations if x[0].normalize() <= today
    ]
    if not observations:
        raise RuntimeError(
            "No Treasury curve observation exists on or before today."
        )

    obs_date, cells = max(observations, key=lambda x: x[0])
    rows = []

    for i, header in enumerate(headers[1:], start=1):
        if i >= len(cells):
            continue
        months = _tenor_months(header)
        yld = _num(cells[i])
        if months is None or yld is None:
            continue

        if months == 1.5:
            maturity = obs_date + pd.Timedelta(days=45)
        else:
            maturity = obs_date + pd.DateOffset(months=int(months))

        security_type = (
            "Treasury Bill Benchmark"
            if months <= 12
            else "Treasury Note/Bond Benchmark"
        )

        rows.append(
            {
                "Security Type": security_type,
                "Maturity": pd.Timestamp(maturity).normalize(),
                "Coupon (%)": None,
                "Bid": None,
                "Asked": None,
                "Asked Yield (%)": yld,
                "Source": "U.S. Treasury Daily Par Yield Curve",
                "Benchmark Tenor": header.title(),
            }
        )

    df = _dedupe(rows)
    if df.empty:
        raise RuntimeError(
            "Official Treasury yield-curve fallback produced no usable rows."
        )

    return df, {
        "source": "U.S. Treasury Daily Par Yield Curve",
        "source_url": url,
        "as_of": pd.Timestamp(obs_date).strftime("%Y-%m-%d"),
        "fallback": True,
        "note": (
            "Fallback benchmark curve used because WSJ individual quote rows "
            "were unavailable. Benchmark dates are synthetic tenor dates, "
            "not exact Treasury security maturities."
        ),
    }


def load_treasury_quotes():
    """
    WSJ first. If WSJ cannot be read without bypassing controls, use the
    official Treasury par-yield curve so the comparison tool remains functional.
    """
    wsj_error = None
    try:
        return load_wsj_treasury_quotes()
    except Exception as exc:
        wsj_error = str(exc)

    df, meta = load_treasury_curve_fallback()
    meta["wsj_error"] = wsj_error
    return df, meta


def nearest_treasury(treasury_df, target_maturity):
    if treasury_df is None or treasury_df.empty:
        return None

    target = pd.Timestamp(target_maturity).normalize()
    work = treasury_df.copy()
    work["Maturity"] = pd.to_datetime(
        work["Maturity"], errors="coerce"
    )
    work = work.dropna(
        subset=["Maturity", "Asked Yield (%)"]
    ).copy()
    if work.empty:
        return None

    work["Maturity Gap Days"] = (
        work["Maturity"] - target
    ).abs().dt.days
    work = work.sort_values(
        ["Maturity Gap Days", "Asked Yield (%)"],
        ascending=[True, False],
        na_position="last",
    )
    return work.iloc[0]


def nearest_muni_candidates(
    muni_df,
    target_maturity,
    states=None,
    limit=25,
):
    target = pd.Timestamp(target_maturity).normalize()
    work = muni_df.copy()

    if states:
        if isinstance(states, str):
            states = [states]
        work = work[work["State"].isin(states)].copy()

    work["Maturity"] = pd.to_datetime(
        work["Maturity"], errors="coerce"
    )
    work["Yield to Worst (%)"] = pd.to_numeric(
        work["Yield to Worst (%)"], errors="coerce"
    )
    work = work.dropna(
        subset=["Maturity", "Yield to Worst (%)"]
    ).copy()

    work["Maturity Gap Days"] = (
        work["Maturity"] - target
    ).abs().dt.days
    work = work.sort_values(
        ["Maturity Gap Days", "Yield to Worst (%)"],
        ascending=[True, False],
        na_position="last",
    )
    return work.head(int(limit)).reset_index(drop=True)


def tax_equivalent_comparison(
    muni_yield,
    treasury_yield,
    federal_tax_rate,
):
    """
    federal_tax_rate is a decimal (e.g., 0.37).

    Assumptions:
    - Muni yield is federally tax-exempt.
    - Treasury interest is federally taxable.
    - State/local taxes are excluded from this first version.
    """
    t = float(federal_tax_rate)
    if not (0 <= t < 1):
        raise ValueError(
            "Federal tax rate must be between 0% and less than 100%."
        )

    muni_yield = float(muni_yield)
    treasury_yield = float(treasury_yield)

    muni_after_tax = muni_yield
    treasury_after_tax = treasury_yield * (1 - t)
    muni_tey = muni_yield / (1 - t)
    after_tax_spread_bps = (
        muni_after_tax - treasury_after_tax
    ) * 100

    winner = (
        "MUNICIPAL"
        if muni_after_tax > treasury_after_tax
        else (
            "TREASURY"
            if treasury_after_tax > muni_after_tax
            else "TIE"
        )
    )

    return {
        "Muni After-Tax Yield (%)": muni_after_tax,
        "Treasury After-Tax Yield (%)": treasury_after_tax,
        "Muni Tax-Equivalent Yield (%)": muni_tey,
        "After-Tax Spread (bps)": after_tax_spread_bps,
        "Winner": winner,
    }
