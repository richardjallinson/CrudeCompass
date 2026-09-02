"""Crude Compass v1C — configuration.

Everything tunable lives here. No API keys in code: put them in a .env file
next to this one, or export them in your shell.

    EIA_API_KEY   free, instant:  https://www.eia.gov/opendata/register.php

CFTC needs no key. Yahoo Finance needs no key. FRED is no longer used (v1C).
"""

import os
from pathlib import Path

# --- paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "crude_compass.db"
# The web app reads this file. Point it at wherever you serve web/ from.
EXPORT_PATH = ROOT.parent / "web" / "data.json"


# --- keys ------------------------------------------------------------------
def _load_dotenv():
    """Minimal .env reader so there is no extra dependency."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

EIA_API_KEY = os.environ.get("EIA_API_KEY", "")
# Kept only so an old .env or an old GitHub secret does not break anything.
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


# --- data sources ----------------------------------------------------------
# Daily prices: all four now come from Yahoo Finance's chart endpoint.
#
# WHY. v1B moved WTI from FRED to Yahoo because FRED's DCOILWTICO lagged
# about four business days, so the 8:00 AM call was about last week. The
# other three series had the same problem and were still on FRED (the
# broad dollar index there is published WEEKLY, so dxy_ret_1 was always a
# week old). v1C puts them all on the same same-day-close feed.
#
# CL=F is the continuous front-month WTI contract. That is also what the
# BetaPro Crude Oil Rolling Futures Index behind HOU/HOD tracks, which
# makes it a better target for that trade than the Cushing spot print.
#
# Storage keys: the value in this dict is the row key in the `prices`
# table. WTI keeps FRED's old id so the existing database and the live
# Scoreboard history carry over untouched (the id is just a label now).
# The other three get new keys so no stale FRED row can mix in.
YAHOO_SERIES = {
    "wti":    {"symbol": "CL=F",     "key": "DCOILWTICO"},
    "brent":  {"symbol": "BZ=F",     "key": "YF_BZ"},
    "dxy":    {"symbol": "DX-Y.NYB", "key": "YF_DXY"},
    "natgas": {"symbol": "NG=F",     "key": "YF_NG"},
}
# Still referenced by name in a couple of places from v1B.
YAHOO_SYMBOL = YAHOO_SERIES["wti"]["symbol"]

# Yahoo chart ranges. Backfill pulls everything; the daily update pulls a
# short trailing window and upserts it over what is already stored.
YAHOO_RANGE_BACKFILL = "max"
YAHOO_RANGE_UPDATE = "3mo"

# Weekly sources (EIA, CFTC): how far back the daily update re-pulls. Both
# publish with a lag and occasionally revise, so a generous window is cheap
# insurance. Days.
UPDATE_LOOKBACK_DAYS = 60

# Legacy. Nothing in v1C fetches from FRED; these stay so old code paths
# that mention FRED resolve to "nothing to do" rather than crash.
FRED_SERIES = {}
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# EIA v2 weekly petroleum stocks.
#   WCESTUS1              US crude oil stocks excluding SPR, thousand barrels
#   W_EPC0_SAX_YCUOK_MBBL Cushing OK crude stocks, thousand barrels
# Cushing matters more than the national number for WTI specifically: it is
# the delivery point the contract settles into.
EIA_BASE = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
EIA_SERIES = {
    "us_crude_stocks": "WCESTUS1",
    "cushing_stocks": "W_EPC0_SAX_YCUOK_MBBL",
}

# CFTC Commitments of Traders, disaggregated futures-only, Socrata endpoint.
# No API key. Filtered to the NYMEX WTI contract by market code.
CFTC_BASE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
CFTC_CONTRACT_MATCH = "CRUDE OIL, LIGHT SWEET"  # matched case-insensitively


# --- the trade this app supports ------------------------------------------
# Shown on the Today card so the lean reads in the instrument actually
# traded. Both are 2x daily and, per their fact sheets, hedge USD back to
# CAD at all times, so the WTI direction is the whole trade: there is no
# separate currency leg to worry about.
INSTRUMENTS = {
    "up":   {"ticker": "HOU.TO", "name": "BetaPro Crude Oil Leveraged Daily Bull"},
    "down": {"ticker": "HOD.TO", "name": "BetaPro Crude Oil Inverse Leveraged Daily Bear"},
    "leverage": 2.0,
}


# --- model -----------------------------------------------------------------
# History start. 2010 onward keeps the sample modern (shale era) while still
# giving ~3,900 trading days.
HISTORY_START = "2010-01-01"

# Stand-down band. Probabilities inside this range produce NO call.
# This is the single most important parameter in the app: widening it makes
# the model quieter and its fired calls better; narrowing it does the reverse.
STAND_DOWN_LOW = 0.45
STAND_DOWN_HIGH = 0.55

# Walk-forward validation: train on everything up to a point, predict the
# next block, step forward, repeat. Never train on the future.
WALKFORWARD_MIN_TRAIN = 750   # ~3 years before the first out-of-sample call
WALKFORWARD_STEP = 21         # refit monthly

# Calibration needs this many prior out-of-sample days before it is trusted.
CALIBRATION_MIN_DAYS = 200

# Expected-range width. 1.0 sigma ~= 68% of days land inside.
RANGE_SIGMA = 1.0
