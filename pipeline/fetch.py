"""Crude Compass v1C — data fetchers.

Three free sources:
    Yahoo Finance  daily WTI, Brent, dollar index, natural gas (same-day close)
    EIA            weekly crude and Cushing stocks
    CFTC           weekly managed-money positioning

FRED is gone as of v1C. Its price series were republished EIA/Fed data with
a lag of several business days, which meant the 8:00 AM call was reasoning
about last week. Yahoo's chart endpoint posts the close the same evening.
It is unofficial and occasionally changes shape; `python run.py
--check-sources` hits every source once and reports exactly what came back,
so run that first after any upgrade.
"""

import datetime as _dt
import time

import requests

import config

TIMEOUT = 30
RETRIES = 3
USER_AGENT = "CrudeCompass/1C (personal research)"


def _get(url, params, label):
    """GET with a couple of retries. Raises RuntimeError with a readable message."""
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(
                url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as exc:  # network, JSON, whatever
            last = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{label} failed after {RETRIES} attempts. {last}")


def _days_ago(days):
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Yahoo Finance - daily closes, no API key
# ---------------------------------------------------------------------------
# Uses the chart JSON endpoint, not the old CSV download, because the CSV
# route now needs a session cookie. Returns the same (date, value) shape
# every other fetcher does, so the storage layer does not care where a
# series came from.
def fetch_yahoo(symbol=None, start=None, range_=None):
    """Returns list of (date_str, float_or_None), oldest first."""
    sym = symbol or config.YAHOO_SYMBOL
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"range": range_ or config.YAHOO_RANGE_BACKFILL, "interval": "1d"}

    data = _get(url, params, f"Yahoo {sym}")

    try:
        result = data["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        err = (data.get("chart") or {}).get("error")
        raise RuntimeError(
            f"Yahoo {sym} returned an unexpected shape ({exc}). "
            f"error field: {err}"
        )

    if not stamps:
        raise RuntimeError(f"Yahoo {sym} returned no rows - check the symbol")

    start = start or config.HISTORY_START
    out = []
    for ts, close in zip(stamps, closes):
        # Yahoo timestamps are UTC seconds at the session open; the date is
        # what matters. A futures session opens the evening before in
        # Eastern time but the bar is stamped on the trading date.
        date = _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        if start and date < start[:10]:
            continue
        val = None if close is None else float(close)
        out.append((date, val))

    # Collapse any duplicate dates, keeping the last value seen, and drop
    # empty bars (Yahoo emits null closes on some holidays).
    best = {}
    for date, val in out:
        if val is None and date in best:
            continue
        best[date] = val
    return sorted(best.items())


# ---------------------------------------------------------------------------
# EIA v2
# ---------------------------------------------------------------------------
def fetch_eia(series_id, start=None):
    """Weekly petroleum stocks. Returns list of (date_str, float_or_None)."""
    if not config.EIA_API_KEY:
        raise RuntimeError(
            "EIA_API_KEY is not set. Get a free key at "
            "https://www.eia.gov/opendata/register.php and put it in pipeline/.env"
        )
    params = {
        "api_key": config.EIA_API_KEY,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[series][]": series_id,
        "start": (start or config.HISTORY_START)[:10],
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
    }
    data = _get(config.EIA_BASE, params, f"EIA {series_id}")
    rows = data.get("response", {}).get("data", [])
    out = []
    for row in rows:
        period = row.get("period")
        val = row.get("value")
        if period is None:
            continue
        out.append((period, None if val is None else float(val)))
    return out


# ---------------------------------------------------------------------------
# CFTC — Commitments of Traders, disaggregated futures-only
# ---------------------------------------------------------------------------
def fetch_cftc(start=None):
    """Managed-money net length for NYMEX WTI.

    Returns list of (date_str, net_contracts).
    """
    params = {
        "$where": f"report_date_as_yyyy_mm_dd >= '{(start or config.HISTORY_START)[:10]}T00:00:00'",
        "$limit": 50000,
        "$order": "report_date_as_yyyy_mm_dd ASC",
    }
    data = _get(config.CFTC_BASE, params, "CFTC COT")

    match = config.CFTC_CONTRACT_MATCH.lower()
    out = []
    for row in data:
        name = (row.get("market_and_exchange_names") or "").lower()
        if match not in name:
            continue
        date = (row.get("report_date_as_yyyy_mm_dd") or "")[:10]
        if not date:
            continue
        try:
            long_ = float(row.get("m_money_positions_long_all", 0) or 0)
            short = float(row.get("m_money_positions_short_all", 0) or 0)
        except (TypeError, ValueError):
            continue
        out.append((date, long_ - short))

    # Several WTI contracts share a family name; collapse duplicates per date
    # by keeping the largest absolute net, which is the main contract.
    best = {}
    for date, net in out:
        if date not in best or abs(net) > abs(best[date]):
            best[date] = net
    return sorted(best.items())


# ---------------------------------------------------------------------------
# Source check
# ---------------------------------------------------------------------------
def check_sources():
    """Hit every source once and report. Run this before trusting anything."""
    results = []

    for label, spec in config.YAHOO_SERIES.items():
        sym = spec["symbol"]
        try:
            rows = fetch_yahoo(sym, range_="1y")
            good = [r for r in rows if r[1] is not None]
            results.append(
                (f"Yahoo {label} ({sym})", True,
                 f"{len(rows)} rows, last good {good[-1][0]} = {good[-1][1]:.2f}" if good else "no values")
            )
        except Exception as exc:
            results.append((f"Yahoo {label} ({sym})", False, str(exc)))

    for label, sid in config.EIA_SERIES.items():
        try:
            rows = fetch_eia(sid, start="2024-01-01")
            results.append(
                (f"EIA {label} ({sid})", True,
                 f"{len(rows)} rows, last {rows[-1][0]} = {rows[-1][1]}" if rows else "no rows")
            )
        except Exception as exc:
            results.append((f"EIA {label} ({sid})", False, str(exc)))

    try:
        rows = fetch_cftc(start="2024-01-01")
        results.append(
            ("CFTC managed money net", True,
             f"{len(rows)} rows, last {rows[-1][0]} = {rows[-1][1]:,.0f}" if rows else "no matching contract rows")
        )
    except Exception as exc:
        results.append(("CFTC managed money net", False, str(exc)))

    return results
