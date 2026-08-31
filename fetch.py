"""Crude Compass v1B — data fetchers.

Three free, commercially usable sources:
    FRED  (St. Louis Fed)  daily WTI, Brent, dollar index, natural gas
    EIA   (US government)  weekly crude and Cushing stocks
    CFTC  (US government)  weekly managed-money positioning

UNTESTED AGAINST LIVE ENDPOINTS. This code was written without network
access, so the request shapes follow each provider's published documentation
but have never actually been run. Expect one or two small fixes on your first
run — a renamed JSON field, a facet parameter, a series ID. Run
`python run.py --check-sources` first: it hits each source once and reports
exactly what came back.
"""

import time
import requests

import config

TIMEOUT = 30
RETRIES = 3
USER_AGENT = "CrudeCompass/1B (personal research)"


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


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------
def fetch_fred(series_id, start=None):
    """Returns list of (date_str, float_or_None), oldest first."""
    if not config.FRED_API_KEY:
        raise RuntimeError(
            "FRED_API_KEY is not set. Get a free key at "
            "https://fredaccount.stlouisfed.org/apikeys and put it in pipeline/.env"
        )
    params = {
        "series_id": series_id,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
        "observation_start": start or config.HISTORY_START,
    }
    data = _get(config.FRED_BASE, params, f"FRED {series_id}")
    out = []
    for obs in data.get("observations", []):
        raw = obs.get("value")
        # FRED writes "." for a missing observation (holidays, etc).
        val = None if raw in (None, ".", "") else float(raw)
        out.append((obs["date"], val))
    return out


# ---------------------------------------------------------------------------
# Yahoo Finance - front-month WTI, no API key, same-day close
# ---------------------------------------------------------------------------
# FRED's DCOILWTICO is official but lags several business days, which makes the
# morning call stale before it is even made. Yahoo carries the same continuous
# front-month contract (CL=F) and posts the close the same evening.
#
# This uses Yahoo's chart JSON endpoint, not the old CSV download, because the
# CSV route now needs a session cookie. Same return shape as fetch_fred(), so
# it is a drop-in replacement for the WTI series.
def fetch_yahoo(symbol=None, start=None):
    """Returns list of (date_str, float_or_None), oldest first."""
    import datetime as _dt

    sym = symbol or config.YAHOO_SYMBOL
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"range": "10y", "interval": "1d"}

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

    out = []
    for ts, close in zip(stamps, closes):
        # Yahoo timestamps are UTC seconds at market open; the date is what matters.
        date = _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        if start and date < start[:10]:
            continue
        val = None if close is None else float(close)
        out.append((date, val))

    # Collapse any duplicate dates, keeping the last value seen.
    best = {}
    for date, val in out:
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

    Returns list of (date_str, net_contracts). The Socrata field names below
    follow the published disaggregated schema; if a field is missing on your
    first run, print one record and adjust — the endpoint is stable but the
    column naming has drifted across CFTC's report generations.
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

    try:
        rows = fetch_yahoo()
        good = [r for r in rows if r[1] is not None]
        results.append(
            (f"Yahoo WTI ({config.YAHOO_SYMBOL})", True,
             f"{len(rows)} rows, last good {good[-1][0]} = {good[-1][1]}" if good else "no values")
        )
    except Exception as exc:
        results.append((f"Yahoo WTI ({config.YAHOO_SYMBOL})", False, str(exc)))

    for label, sid in config.FRED_SERIES.items():
        try:
            rows = fetch_fred(sid, start="2024-01-01")
            good = [r for r in rows if r[1] is not None]
            results.append(
                (f"FRED {label} ({sid})", True,
                 f"{len(rows)} rows, last good {good[-1][0]} = {good[-1][1]}" if good else "no values")
            )
        except Exception as exc:
            results.append((f"FRED {label} ({sid})", False, str(exc)))

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
