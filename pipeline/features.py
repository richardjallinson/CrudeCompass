"""Crude Compass v1C — features.

Turns the stored series into one daily table the model can train on.

THE CARDINAL RULE, enforced throughout: every feature for date D uses only
information available BEFORE the 8:00 AM lock on date D. Weekly series are
shifted forward past their publication lag; daily price features use the
prior settlement, never the current one. Getting this wrong produces a
model with a wonderful backtest and no real skill, which is the single most
common way a project like this fools its owner.

WHAT IS NOT HERE YET, and why it matters:

  Term structure (M1-M3 futures spread). Probably the single best
  fundamental input, and still parked. Yahoo serves each contract only for
  its own life, so a history long enough to VALIDATE the feature is not
  available for free. Adding it unvalidated would reset the live Scoreboard
  clock for a feature nobody has measured. A continuous back-adjusted series
  (Databento / Barchart / CME) is the v1D route.

  Inventory SURPRISE (EIA actual vs. consensus). The surprise moves price,
  not the level. Consensus forecasts are a paid product. v1B uses the level
  and the change instead, which is a weaker substitute.

  News scoring. v1C.
"""

import numpy as np
import pandas as pd

import config
import db


def _series_frame(table, series, name):
    rows = db.read_series(table, series)
    if not rows:
        return pd.DataFrame(columns=["date", name]).set_index("date")
    df = pd.DataFrame(rows, columns=["date", name])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def build():
    """Returns a DataFrame indexed by date with features + target."""
    S = config.YAHOO_SERIES
    wti = _series_frame("prices", S["wti"]["key"], "wti")
    brent = _series_frame("prices", S["brent"]["key"], "brent")
    dxy = _series_frame("prices", S["dxy"]["key"], "dxy")
    natgas = _series_frame("prices", S["natgas"]["key"], "natgas")

    df = wti.join([brent, dxy, natgas], how="left")
    # Holidays and the odd missing bar: carry the last known price forward,
    # then drop any leading rows that never had one.
    df = df.ffill().dropna(subset=["wti"])

    # --- the target -------------------------------------------------------
    # Did WTI close higher than the prior close? This is what the app calls.
    df["ret"] = df["wti"].pct_change()
    df["target"] = (df["ret"] > 0).astype(int)

    # --- daily price features (all lagged) --------------------------------
    # .shift(1) everywhere: on the morning of D we know through D-1 only.
    df["ret_1"] = df["ret"].shift(1)
    df["ret_2"] = df["ret"].shift(2)
    df["ret_5"] = df["wti"].pct_change(5).shift(1)
    df["ret_20"] = df["wti"].pct_change(20).shift(1)

    # Momentum: short trend against medium trend.
    ma5 = df["wti"].rolling(5).mean()
    ma20 = df["wti"].rolling(20).mean()
    df["ma_gap"] = ((ma5 - ma20) / ma20).shift(1)

    # Realized volatility, annualised-ish. Drives the expected range.
    df["vol_20"] = df["ret"].rolling(20).std().shift(1)
    df["vol_5"] = df["ret"].rolling(5).std().shift(1)
    # Volatility regime: is short vol above or below its own recent norm?
    df["vol_ratio"] = (df["vol_5"] / df["vol_20"]).shift(0)

    # Mean reversion pressure: distance from the 20-day mean in sigmas.
    df["z_20"] = ((df["wti"] - ma20) / df["wti"].rolling(20).std()).shift(1)

    # --- cross-market -----------------------------------------------------
    df["dxy_ret_1"] = df["dxy"].pct_change().shift(1)
    df["dxy_ret_5"] = df["dxy"].pct_change(5).shift(1)
    df["brent_wti"] = (df["brent"] - df["wti"]).shift(1)
    df["brent_wti_chg"] = (df["brent"] - df["wti"]).diff().shift(1)
    df["natgas_ret_5"] = df["natgas"].pct_change(5).shift(1)

    # --- weekly fundamentals ----------------------------------------------
    # EIA publishes Wednesday 10:30 ET for the week ending the prior Friday.
    # Shifting by 5 business days keeps the 8:00 AM lock honest: a Wednesday
    # morning prediction must NOT see that morning's report.
    for key, sid in config.EIA_SERIES.items():
        w = _series_frame("weekly", sid, key)
        if w.empty:
            df[f"{key}_chg"] = 0.0
            continue
        w[f"{key}_chg"] = w[key].diff()
        joined = w.reindex(df.index, method="ffill")
        df[f"{key}_chg"] = joined[f"{key}_chg"].shift(5).fillna(0.0)

    # CFTC publishes Friday 15:30 ET for the Tuesday position. Shift past it.
    cot = _series_frame("weekly", "cftc_mm_net", "mm_net")
    if cot.empty:
        df["mm_net_pct"] = 0.5
        df["mm_net_chg"] = 0.0
    else:
        joined = cot.reindex(df.index, method="ffill")["mm_net"].shift(3)
        # Percentile of net length within a trailing 3-year window: the
        # "how crowded is this trade" gauge. Levels are not comparable
        # across eras; percentiles are.
        df["mm_net_pct"] = joined.rolling(756, min_periods=60).apply(
            lambda x: (x[-1] > x[:-1]).mean() if len(x) > 1 else 0.5, raw=True
        )
        df["mm_net_chg"] = joined.diff()

    # --- calendar ---------------------------------------------------------
    dow = df.index.dayofweek
    for i, name in enumerate(["mon", "tue", "wed", "thu", "fri"]):
        df[f"dow_{name}"] = (dow == i).astype(float)
    # Month-of-year as two smooth terms rather than 12 dummies: seasonality
    # in oil is broad (driving season, maintenance), not calendar-sharp.
    month_angle = 2 * np.pi * (df.index.month - 1) / 12.0
    df["seas_sin"] = np.sin(month_angle)
    df["seas_cos"] = np.cos(month_angle)

    df = df.replace([np.inf, -np.inf], np.nan)
    return df


FEATURES = [
    "ret_1", "ret_2", "ret_5", "ret_20",
    "ma_gap", "vol_20", "vol_ratio", "z_20",
    "dxy_ret_1", "dxy_ret_5",
    "brent_wti", "brent_wti_chg", "natgas_ret_5",
    "us_crude_stocks_chg", "cushing_stocks_chg",
    "mm_net_pct", "mm_net_chg",
    "dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri",
    "seas_sin", "seas_cos",
]

# Human-readable labels for the app's "Why" list.
DRIVER_LABELS = {
    "ret_1": "Yesterday's move",
    "ret_2": "Two-day drift",
    "ret_5": "Five-day trend",
    "ret_20": "Twenty-day trend",
    "ma_gap": "Short trend vs medium trend",
    "vol_20": "Realized volatility",
    "vol_ratio": "Volatility regime",
    "z_20": "Distance from 20-day mean",
    "dxy_ret_1": "Dollar, one day",
    "dxy_ret_5": "Dollar, five days",
    "brent_wti": "Brent-WTI spread",
    "brent_wti_chg": "Brent-WTI spread change",
    "natgas_ret_5": "Natural gas, five days",
    "us_crude_stocks_chg": "US crude stocks change",
    "cushing_stocks_chg": "Cushing stocks change",
    "mm_net_pct": "Managed-money crowding",
    "mm_net_chg": "Positioning change",
    "dow_mon": "Day of week", "dow_tue": "Day of week", "dow_wed": "Day of week",
    "dow_thu": "Day of week", "dow_fri": "Day of week",
    "seas_sin": "Seasonality", "seas_cos": "Seasonality",
}


def matrix(df):
    """Returns (X, y, index) with rows that have every feature present."""
    cols = [c for c in FEATURES if c in df.columns]
    sub = df[cols + ["target"]].dropna()
    return sub[cols].to_numpy(), sub["target"].to_numpy(), sub.index, cols
