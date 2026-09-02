#!/usr/bin/env python3
"""Crude Compass v1C — the runner (run.py, revision 3: density check + chunked backfill).

    python run.py --check-sources   hit every API once, report what came back
    python run.py --backfill        download all history into the database
    python run.py --update          refresh the trailing window only (fast)
    python run.py --validate        walk-forward test. THE important one.
    python run.py --daily           update, then produce today's locked call
    python run.py --demo            run everything on synthetic data, no keys

First time on v1C: --check-sources, then --backfill (the Brent, dollar and
natural-gas series moved to new storage keys), then --validate. After that,
--daily does its own refresh every morning; --backfill is only for repair.
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


def _pull(full):
    """Shared by --backfill (full=True) and --update (full=False).

    Full pulls everything from HISTORY_START. Update pulls a short trailing
    window and upserts it: same rows overwritten with the same values, new
    rows added, nothing older touched. The prediction log is a separate
    table and is never written here.
    """
    db.init()
    if full:
        print(f"Backfilling from {config.HISTORY_START}.\n")
        yrange, since = config.YAHOO_RANGE_BACKFILL, None
    else:
        since = fetch._days_ago(config.UPDATE_LOOKBACK_DAYS)
        print(f"Updating the trailing {config.UPDATE_LOOKBACK_DAYS} days (since {since}).\n")
        yrange = config.YAHOO_RANGE_UPDATE

    want = fetch.expected_rows(config.HISTORY_START) if full else 0
    short = []
    for label, spec in config.YAHOO_SERIES.items():
        if full:
            # Wide date ranges get silently downsampled by Yahoo on some
            # symbol types if pulled in one request - see fetch_yahoo_history.
            rows = fetch.fetch_yahoo_history(spec["symbol"], start=config.HISTORY_START)
        else:
            rows = fetch.fetch_yahoo(spec["symbol"], start=since)
        n = db.upsert_series("prices", spec["key"], rows)
        good = [r for r in rows if r[1] is not None]
        last = f"last {good[-1][0]} = {good[-1][1]:.2f}" if good else "no values"
        flag = ""
        if full and n < want * 0.5:
            flag = f"   <-- SHORT, expected about {want}"
            short.append(label)
        print(f"  Yahoo {label:8s} {spec['symbol']:10s} {n:6d} rows   {last}{flag}")

    for label, sid in config.EIA_SERIES.items():
        rows = fetch.fetch_eia(sid, start=since)
        n = db.upsert_series("weekly", sid, rows)
        print(f"  EIA   {label:8s} {sid:24s} {n:6d} rows")

    rows = fetch.fetch_cftc(start=since)
    n = db.upsert_series("weekly", "cftc_mm_net", rows)
    print(f"  CFTC  managed-money net             {n:6d} rows")
    return short


def cmd_backfill():
    short = _pull(full=True)
    print("\nBackfill complete.")
    if short:
        print()
        print("WARNING: these series came back far shorter than expected: "
              + ", ".join(short) + ".")
        print("A short series does not fail the run - it quietly shrinks the")
        print("sample the model trains and scores on, which usually FLATTERS")
        print("the accuracy number. Do not trust the walk-forward line above")
        print("until this is fixed.")
    return 0


def _needs_backfill():
    """True if any price series is missing, starts late, or is too sparse.

    Two independent checks, because either alone missed a real failure:
      - starts late: catches an empty series, or one that only has a
        recent window (a genuinely short pull).
      - too sparse: catches a series that spans the full history but at
        the wrong density - the Yahoo downsampling bug this app hit,
        where a 16-year span came back as ~170 monthly-ish bars with an
        old earliest date that looked fine by the first check alone.
    """
    db.init()
    cutoff = (pd.Timestamp(config.HISTORY_START) + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    want = fetch.expected_rows(config.HISTORY_START)
    for label, spec in config.YAHOO_SERIES.items():
        rows = db.read_series("prices", spec["key"])
        if not rows or rows[0][0] > cutoff:
            print(f"Series {label} ({spec['key']}) is missing or short; a full backfill is needed.")
            return True
        if len(rows) < want * 0.5:
            print(f"Series {label} ({spec['key']}) has {len(rows)} rows, expected about {want}; "
                  f"a full backfill is needed.")
            return True
    return False


def cmd_update():
    _pull(full=False)
    print("\nUpdate complete.")
    return 0


def _load_frame():
    df = feat.build()
    if df.empty or len(df) < config.WALKFORWARD_MIN_TRAIN:
        print("Not enough data. Run --backfill first.", file=sys.stderr)
        sys.exit(1)
    return df


def _score(X, y):
    """Walk-forward probabilities, their calibrated twins, and the scorecard."""
    probs, mask = mdl.walk_forward(X, y)
    cal_probs, cal_mask = mdl.calibrate_walk_forward(probs, y, mask)
    ev = mdl.evaluate(probs, y, mask, cal_probs, cal_mask)
    return probs, mask, cal_probs, cal_mask, ev


def cmd_validate(df=None):
    df = df if df is not None else _load_frame()
    X, y, idx, cols = feat.matrix(df)
    print(f"Feature matrix: {X.shape[0]} days, {X.shape[1]} features")
    print(f"Range: {idx[0].date()} to {idx[-1].date()}\n")

    probs, mask, cal_probs, cal_mask, ev = _score(X, y)
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
    if ev["scored_calibrated"]:
        print(f"  Brier score (calibrated)  {ev['brier']:.4f}  (baseline {ev['brier_baseline']:.4f}, "
              f"lower is better; {ev['n_scored']} scored days)")
    else:
        print(f"  Brier score (raw)         {ev['brier']:.4f}  (baseline {ev['brier_baseline']:.4f}, lower is better)")
    print()
    if ev["calibration"]:
        print("  CALIBRATION" + ("  (calibrated probabilities, no look-ahead)" if ev["scored_calibrated"] else ""))
        for c in ev["calibration"]:
            print(f"    {c['band']:8s} said {c['predicted']*100:5.1f}%  ->  was {c['actual']*100:5.1f}%   n={c['n']}")
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
    print()
    print("  v1B reference (FRED->Yahoo swap, Aug 31 2026): fired 53.17% vs")
    print("  baseline 50.33%, edge +2.84, Brier 0.2524. If v1C lands well")
    print("  below that, the Brent/dollar/natgas source change cost something")
    print("  and the two-source setup should be revisited before shipping.")
    return 0


def cmd_daily(df=None, dry=False):
    # A fresh call needs fresh data. Refresh the trailing window first, so
    # the morning run is one command and never depends on a full backfill.
    if df is None and not dry:
        if _needs_backfill():
            cmd_backfill()
        else:
            cmd_update()
        print()
    df = df if df is not None else _load_frame()
    X, y, idx, cols = feat.matrix(df)

    # Validate first so the app can display an honest scorecard.
    probs, mask, cal_probs, cal_mask, ev = _score(X, y)

    # Fit on everything through the last complete day, then predict the next.
    final = mdl.fit_final(X, y)
    last_row = X[-1]
    raw_prob, ranked = mdl.predict_one(final, last_row, cols)
    state = mdl.state_for(raw_prob)

    # Calibrate the DISPLAYED number, not the fire/stand-down decision. The
    # fire decision is validated against raw thresholds; changing what
    # triggers a call would invalidate that measurement. Calibration fixes a
    # different problem: whether "58%" on the dial resolves 58% of the time.
    # Fit on ALL out-of-sample history here; that is the map the Scoreboard
    # also grades (walk-forward, without look-ahead) so display and score
    # are the same number.
    calibrator = mdl.fit_calibrator(probs[mask], y[mask])
    prob = mdl.calibrate(calibrator, raw_prob)
    calibration_applied = calibrator is not None

    # Honesty flag. A raw score can clear the stand-down band while its
    # calibrated twin lands right at even, which shows on the dial as
    # "50% DOWN". The call is legitimate - it fired on the validated rule -
    # but the trader should know the calibrated odds are near a coin flip.
    near_even = state != "none" and abs(prob - 0.5) < 0.03

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

    # Next BUSINESS day. A Friday data row must produce a Monday session,
    # not a Saturday that can never resolve.
    session_date = (idx[-1] + pd.offsets.BDay(1)).date()

    lev = config.INSTRUMENTS["leverage"]
    prediction = {
        "state": state,
        "probability": round(prob, 4),
        "rawScore": round(raw_prob, 4),
        "nearEven": bool(near_even),
        "rangeLow": round(range_low, 2),
        "rangeHigh": round(range_high, 2),
        # The range as a percent move, and what 2x of it looks like. That is
        # the number that matters for sizing a HOU/HOD day trade.
        "rangePct": round(sigma * config.RANGE_SIGMA * 100, 2),
        "etfRangePct": round(sigma * config.RANGE_SIGMA * lev * 100, 2),
        "instrument": (config.INSTRUMENTS[state]["ticker"] if state in ("up", "down") else None),
        "drivers": drivers,
        "caution": (
            "Resolution happens the next morning, not live at the 2:30 PM ET "
            "settlement: the pipeline runs once, before the open. Around scheduled "
            "releases the model's read applies to the session as a whole, not to "
            "the minutes after the print."
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

    # A one-line scorecard so the morning log (and the Actions log) shows
    # the validation numbers without a separate --validate run.
    if ev:
        print(f"\nWalk-forward: fired {ev['accuracy_when_fired']*100:.2f}% vs baseline "
              f"{ev['baseline_up_rate']*100:.2f}% on {ev['n_out_of_sample']} days; "
              f"Brier {ev['brier']:.4f} on {ev['n_scored']} scored days.")

    print(f"\nSession {session_date}")
    print(f"  {state.upper():5s}  {prob*100:.1f}% calibrated (raw {raw_prob*100:.1f}%)   "
          f"range ${range_low:.2f}-${range_high:.2f}")
    if prediction["instrument"]:
        print(f"  Instrument: {prediction['instrument']}   ~{prediction['etfRangePct']:.1f}% expected range at {lev:.0f}x")
    if near_even:
        print("  NOTE: calibrated odds are within 3 points of even.")
    for d in drivers:
        print(f"    [{d['dir']:4s}] {d['label']}")
    print(f"\nWrote {path}")
    return 0


def cmd_demo():
    """Everything end-to-end on synthetic data. No API keys, no network.

    The synthetic series has no real signal in it, so the validation SHOULD
    come back at roughly coin-flip: that is the correct result and a good
    sanity check on the validation itself.
    """
    print("DEMO MODE \u2014 synthetic data, no network, no keys.\n")
    rng = np.random.default_rng(7)
    n = 2600
    dates = pd.bdate_range("2014-01-02", periods=n)

    vol = 0.018 * (1 + 0.4 * np.sin(np.arange(n) / 90.0))
    rets = rng.normal(0.0002, 1.0, n) * vol
    wti = 80 * np.exp(np.cumsum(rets))
    brent = wti + 3.5 + rng.normal(0, 0.4, n)
    dxy = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    gas = 3 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))

    db.init()
    S = config.YAHOO_SERIES
    for key, series in (
        (S["wti"]["key"], wti),
        (S["brent"]["key"], brent),
        (S["dxy"]["key"], dxy),
        (S["natgas"]["key"], gas),
    ):
        db.upsert_series("prices", key, [(str(d.date()), float(v)) for d, v in zip(dates, series)])

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
    ap = argparse.ArgumentParser(description="Crude Compass v1C pipeline")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check-sources", action="store_true")
    g.add_argument("--backfill", action="store_true")
    g.add_argument("--update", action="store_true")
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
        elif args.update:
            rc = cmd_update()
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
