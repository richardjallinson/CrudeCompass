"""Crude Compass v1B — configuration.

Everything tunable lives here. No API keys in code: put them in a .env file
next to this one, or export them in your shell.

    EIA_API_KEY   free, instant:  https://www.eia.gov/opendata/register.php
    FRED_API_KEY  free, instant:  https://fredaccount.stlouisfed.org/apikeys

CFTC needs no key at all.
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
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


# --- data sources ----------------------------------------------------------
# FRED daily series. These are the free, official, commercially usable ones.
#
# NOTE: series IDs are stable at FRED but VERIFY each one loads before
# trusting a run. `python run.py --check-sources` does exactly that.
FRED_SERIES = {
    # WTI spot, Cushing OK, USD/bbl, daily. The app's headline price in v1B.
    "wti": "DCOILWTICO",
    # Brent spot, for the Brent-WTI spread.
    "brent": "DCOILBRENTEU",
    # Broad trade-weighted US dollar index, daily.
    "dxy": "DTWEXBGS",
    # Henry Hub natural gas, daily. Weak signal alone, cheap to carry.
    "natgas": "DHHNGSP",
}
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# --- Yahoo Finance: same-day WTI -------------------------------------------
# FRED publishes DCOILWTICO several business days late, which makes the 8am
# call stale before it is made. Yahoo carries the continuous front-month WTI
# contract as CL=F and posts the close the same evening, free and keyless.
# Stooq was tried first and blocks its CSV endpoint from ordinary connections.
YAHOO_SYMBOL = "CL=F"

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

# Expected-range width. 1.0 sigma ~= 68% of days land inside.
RANGE_SIGMA = 1.0
