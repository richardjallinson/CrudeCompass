#!/usr/bin/env python3
"""Crude Compass v1B — the runner.

    python run.py --check-sources   hit every API once, report what came back
    python run.py --backfill        download all history into the database
    python run.py --validate        walk-forward test. THE important one.
    python run.py --daily           produce today's locked call + data.json
    python run.py --demo            run everything on synthetic data, no keys

Start with --check-sources, then --backfill, then --validate. Only wire the
app to real output once --validate shows the model beating its baseline.
"""

import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import db
import fetch
import features as feat
import model as mdl
import export


def cmd_check_sources():
    print("Checking data sources.\n")
    ok = True
    for name, good, detail in fetch.check_sources():
        mark = "  OK  " if good else " FAIL "
        print(f"[{mark}] {name}\n         {detail}")
        ok = ok and good
    print("\nAll sources reachable." if ok else
          "\nSome sources failed. Fix those before backfilling.")
    return 0 if ok else 1


def cmd_backfill():
    db.init()
    print(f"Backfilling from {config.HISTORY_START}.\n")

    for label, sid in config.FRED_SERIES.items():
        rows = fetch.fetch_fred(sid)
        n = db.upsert_series("prices", sid, rows)
        print(f"  FRED {label:8s} {sid:16s} {n:6d} rows")

    for label, sid in config.EIA_SERIES.items():
        rows = fetch.fetch_eia(sid)
        n = db.upsert_series("weekly", sid, rows)
        print(f"  EIA  {label:8s} {sid:24s} {n:6d} rows")

    rows = fetch.fetch_cftc()
    n = db.upsert_series("weekly", "cftc_mm_net", rows)
    print(f"  CFTC managed-money net              {n:6d} rows")

    print("\nBackfill complete.")
    return 0


def _load_frame():
    df = feat.build()
    if df.empty or len(df) < config.WALKFORWARD_MIN_TRAIN:
        print("Not enough data. Run --backfill first.", file=sys.stderr)
        sys.exit(1)
    return df


def cmd_validate(df=None):
    df = df if df is not None else _load_frame()
    X, y, idx, cols = feat.matrix(df)
    print(f"Feature matrix: {X.shape[0]} days, {X.shape[1]} features")
    print(f"Range: {idx[0].date()} to {idx[-1].date()}\n")

    probs, mask = mdl.walk_forward(X, y)
    ev = mdl.evaluate(probs, y, mask)
    if ev is None:
        print("Not enough out-of-sample days to evaluate.", file=sys.stderr)
        return 1

    print("WALK-FORWARD RESULTS (never trained on a day it predicted)")
    print("-" * 58)
    print(f"  Out-of-sample days        {ev['n_out_of_sample']}")
    print(f"  Baseline (always up)      {ev['baseline_up_rate']*100:.2f}%")
    print(f"  Model, all days           {ev['accuracy_all_days']*100:.2f}%")
    print(f"  Model, when it fired      {ev['accuracy_when_fired']*100:.2f}%  "
          f"({ev['n_fired']} calls, {ev['n_stood_down']} stand-downs)")
    print(f"  Brier score               {ev['brier']:.4f}  (baseline {ev['brier_baseline']:.4f}, lower is better)")
    print()
    if ev["calibration"]:
        print("  CALIBRATION")
        for c in ev["calibration"]:
            print(f"    predicted {c['predicted']*100:5.1f}%  ->  actual {c['actual']*100:5.1f}%   n={c['n']}")
        print()

    edge = (ev["accuracy_when_fired"] - ev["baseline_up_rate"]) * 100
    print("-" * 58)
    if ev["beats_baseline"] and edge >= 2.0:
        print(f"  VERDICT: the model beats the baseline by {edge:+.2f} points when it fires.")
        print("  That is a real if thin edge. Paper trade it for 3-6 months")
        print("  before it influences a single dollar.")
    elif ev["beats_baseline"]:
        print(f"  VERDICT: the model edges the baseline by {edge:+.2f} points. That is")
        print("  inside the noise on this sample size. Treat it as no edge until")
        print("  more out-of-sample days accumulate.")
    else:
        print(f"  VERDICT: the model does NOT beat the baseline ({edge:+.2f} points).")
        print("  Ship Crude Compass without the Today card: the briefing, the")
        print("  calendar and the range forecast all stand on their own. That")
        print("  is a real outcome, not a failure - and it is exactly what this")
        print("  validation exists to tell you.")
    print("-" * 58)

    # Calibration check, held out honestly: fit the isotonic map on the
    # FIRST 60% of out-of-sample days only, then test it on the LAST 40% -
    # days the calibrator itself never saw. Anything else would be grading
    # the calibrator's homework with the answer key already in hand.
    oos_probs, oos_y = probs[mask], y[mask]
    n_oos = len(oos_probs)
    split = int(n_oos * 0.6)
    if split >= 200 and (n_oos - split) >= 100:
        calibrator = mdl.fit_calibrator(oos_probs[:split], oos_y[:split])
        test_probs, test_y = oos_probs[split:], oos_y[split:]
        raw_brier = float(np.mean((test_probs - test_y) ** 2))
        cal_probs = np.array([mdl.calibrate(calibrator, p) for p in test_probs])
        cal_brier = float(np.mean((cal_probs - test_y) ** 2))
        print()
        print("CALIBRATION CHECK (isotonic, fit on the first 60% of out-of-sample")
        print("days, tested on the last 40% it never saw)")
        print(f"  Raw Brier on held-out days          {raw_brier:.4f}")
        print(f"  Calibrated Brier on held-out days   {cal_brier:.4f}")
        if cal_brier < raw_brier:
            print("  Calibration helps: apply it before showing a probability to a person.")
        else:
            print("  Calibration did not help on this split. Showing raw probabilities")
            print("  for now; revisit once more history has accumulated.")
    else:
        print()
        print("Not enough out-of-sample days yet for an honest calibration check")
        print("(need >=200 to fit, >=100 held out to test). --daily will show raw")
        print("probabilities until there is enough history.")

    return 0


def cmd_daily(df=None, dry=False):
    df = df if df is not None else _load_frame()
    X, y, idx, cols = feat.matrix(df)

    # Validate first so the app can display an honest scorecard. The scoreboard
    # grades the CALIBRATED probability, because that is what the Today card
    # shows — but calibrated walk-forward, so no day is graded by a calibrator
    # that had already seen it.
    probs, mask = mdl.walk_forward(X, y)
    cal_probs, cal_mask = mdl.calibrate_walk_forward(probs, mask, y)
    ev = mdl.evaluate(probs, y, mask, display_probs=cal_probs, display_mask=cal_mask)

    # Fit on everything through the last complete day, then predict the next.
    final = mdl.fit_final(X, y)
    last_row = X[-1]
    raw_prob, ranked = mdl.predict_one(final, last_row, cols)
    state = mdl.state_for(raw_prob)

    # Calibrate the DISPLAYED number, not the fire/stand-down decision. The
    # 52.98%-vs-baseline result was measured against raw thresholds, so
    # changing what triggers a call would invalidate that measurement. What
    # calibration fixes is a different problem: whether "58%" on the dial
    # actually resolves 58% of the time. Fit on ALL available out-of-sample
    # history here (unlike the held-out split in --validate, which exists
    # only to prove the technique works before trusting it in production).
    calibrator = mdl.fit_calibrator(probs[mask], y[mask])
    prob = mdl.calibrate(calibrator, raw_prob)
    calibration_applied = calibrator is not None

    # Expected range from realized volatility.
    last_price = float(df["wti"].iloc[-1])
    sigma = float(df["vol_20"].iloc[-1] or 0.02)
    span = last_price * sigma * config.RANGE_SIGMA
    range_low, range_high = last_price - span, last_price + span

    # Drivers: the top contributions, in the model's own terms.
    feature_values = {c: float(df[c].iloc[-1]) for c in cols if c in df.columns}
    drivers = []
    seen_labels = set()
    for name, contrib in ranked:
        label = feat.DRIVER_LABELS.get(name, name)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        drivers.append(export._fmt_driver(name, contrib, feature_values.get(name, 0.0)))
        if len(drivers) >= 4:
            break

    # Next TRADING day, not next calendar day. If the last complete row is a
    # Friday, +1 day would stamp the call for a Saturday session that never
    # settles — the row would sit "open" in the scoreboard forever. This does
    # not know about market holidays; a call stamped for Thanksgiving still
    # waits an extra day for its settlement, which resolve_predictions handles
    # because it matches on date rather than assuming the next row is the one.
    session_date = (idx[-1] + pd.offsets.BDay(1)).date()
    prediction = {
        "state": state,
        "probability": round(prob, 4),
        "rangeLow": round(range_low, 2),
        "rangeHigh": round(range_high, 2),
        "drivers": drivers,
        "caution": (
            "Resolution happens the next morning in this version, not live at the "
            "2:30 PM ET settlement \u2014 free data publishes end of day. "
            "Around scheduled releases the model's read applies to the session as a "
            "whole, not to the minutes after the print."
        ),
    }

    model_meta = {
        "kind": "logistic regression (L2, standardized)",
        "features": len(cols),
        "trainedThrough": str(idx[-1].date()),
        "standDownBand": [config.STAND_DOWN_LOW, config.STAND_DOWN_HIGH],
        "lockTime": "08:00 America/New_York",
        "probabilityCalibrated": calibration_applied,
        "calibrationNote": (
            "The UP/DOWN/NO EDGE decision is made on the model's raw score; only "
            "the displayed percentage is calibrated, via isotonic regression "
            "against realized outcomes, so it means what it says."
            if calibration_applied else
            "Not enough out-of-sample history yet to calibrate the displayed "
            "percentage. It is shown raw for now - treat it as a ranking, not a "
            "literal frequency, until more days accumulate."
        ),
    }

    if not dry:
        db.init()
        db.save_prediction({
            "date": str(session_date),
            "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "state": state,
            "probability": prob,
            "reference_price": last_price,
            "range_low": range_low,
            "range_high": range_high,
            "drivers_json": str(drivers),
        })
        # Resolve anything still open against the settlements we now have.
        settlements = {str(d.date()): float(v) for d, v in df["wti"].items()}
        filled = db.resolve_predictions(settlements)
        if filled:
            print(f"Resolved {filled} previously open call(s).")

    payload = export.build_payload(df, cols, prediction, ev, model_meta)
    path = export.write(payload)

    print(f"\nSession {session_date}")
    print(f"  {state.upper():5s}  {prob*100:.1f}%   range ${range_low:.2f}-${range_high:.2f}")
    for d in drivers:
        print(f"    [{d['dir']:4s}] {d['label']}")
    print(f"\nWrote {path}")
    return 0


def cmd_demo():
    """Everything end-to-end on synthetic data. No API keys, no network.

    This exists so the pipeline can be proven to RUN before you have keys,
    and so a failure on real data is clearly a data problem rather than a
    code problem. The synthetic series has no real signal in it, so the
    validation SHOULD come back at roughly coin-flip: that is the correct
    result and a good sanity check on the validation itself.
    """
    print("DEMO MODE \u2014 synthetic data, no network, no keys.\n")
    rng = np.random.default_rng(7)
    n = 2600
    dates = pd.bdate_range("2014-01-02", periods=n)

    # Random walk with mild vol clustering, priced like WTI.
    vol = 0.018 * (1 + 0.4 * np.sin(np.arange(n) / 90.0))
    rets = rng.normal(0.0002, 1.0, n) * vol
    wti = 80 * np.exp(np.cumsum(rets))
    brent = wti + 3.5 + rng.normal(0, 0.4, n)
    dxy = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    gas = 3 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))

    db.init()
    for sid, series in (
        (config.FRED_SERIES["wti"], wti),
        (config.FRED_SERIES["brent"], brent),
        (config.FRED_SERIES["dxy"], dxy),
        (config.FRED_SERIES["natgas"], gas),
    ):
        db.upsert_series("prices", sid, [(str(d.date()), float(v)) for d, v in zip(dates, series)])

    weeks = pd.date_range(dates[0], dates[-1], freq="W-WED")
    for sid, base, scale in (
        (config.EIA_SERIES["us_crude_stocks"], 430000, 4000),
        (config.EIA_SERIES["cushing_stocks"], 40000, 1200),
    ):
        vals = base + np.cumsum(rng.normal(0, scale, len(weeks)))
        db.upsert_series("weekly", sid, [(str(d.date()), float(v)) for d, v in zip(weeks, vals)])

    cot_weeks = pd.date_range(dates[0], dates[-1], freq="W-TUE")
    net = 300000 + np.cumsum(rng.normal(0, 12000, len(cot_weeks)))
    db.upsert_series("weekly", "cftc_mm_net", [(str(d.date()), float(v)) for d, v in zip(cot_weeks, net)])

    df = feat.build()
    print(f"Built {len(df)} rows of synthetic history.\n")
    cmd_validate(df)
    print()
    cmd_daily(df, dry=True)
    print("\nDemo complete. On synthetic noise the verdict SHOULD be 'no edge' \u2014")
    print("if it were not, the validation would be broken.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Crude Compass v1B pipeline")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check-sources", action="store_true")
    g.add_argument("--backfill", action="store_true")
    g.add_argument("--validate", action="store_true")
    g.add_argument("--daily", action="store_true")
    g.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        if args.check_sources:
            return cmd_check_sources()
        if args.backfill:
            rc = cmd_backfill()
        elif args.validate:
            rc = cmd_validate()
        elif args.daily:
            rc = cmd_daily()
        else:
            rc = cmd_demo()
        db.log_run(started, rc == 0)
        return rc
    except Exception as exc:
        try:
            db.log_run(started, False, f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
