"""Crude Compass v1B — export.

Writes web/data.json in exactly the shape app.js expects. The app falls back
to its built-in sample data if this file is missing or malformed, so a failed
pipeline run degrades to "sample data" rather than a blank screen.
"""

import json
from datetime import datetime, timezone

import numpy as np

import config
import db
import features as feat


LOG_ROWS = 30


def _fmt_driver(name, contribution, value):
    label = feat.DRIVER_LABELS.get(name, name)
    direction = "up" if contribution > 0.02 else "down" if contribution < -0.02 else "none"
    return {"label": label, "dir": direction, "note": _driver_note(name, value, direction)}


def _driver_note(name, value, direction):
    """Plain-English gloss. Written from the trader's side of the screen."""
    pushing = {"up": "Pushing the lean up.", "down": "Pushing the lean down.", "none": "Close to neutral today."}[direction]
    try:
        if name in ("ret_1", "ret_2", "ret_5", "ret_20", "dxy_ret_1", "dxy_ret_5", "natgas_ret_5", "ma_gap"):
            return f"{value * 100:+.2f}% over the window. {pushing}"
        if name == "mm_net_pct":
            return f"Managed-money net length sits at the {value * 100:.0f}th percentile of the last three years. {pushing}"
        if name in ("cushing_stocks_chg", "us_crude_stocks_chg"):
            return f"{value:+,.0f} thousand barrels on the latest weekly report. {pushing}"
        if name in ("brent_wti", "brent_wti_chg"):
            return f"${value:+.2f}. {pushing}"
        if name == "z_20":
            return f"{value:+.2f} sigma from the 20-day mean. {pushing}"
        if name in ("vol_20", "vol_ratio"):
            return f"{value:.3f}. {pushing}"
        return f"{value:+.4f}. {pushing}"
    except (TypeError, ValueError):
        return pushing


def build_payload(df, cols, prediction, evaluation, model_meta):
    """Assemble the whole data.json."""
    last_date = df.index[-1]
    wti_now = float(df["wti"].iloc[-1])
    wti_prev = float(df["wti"].iloc[-2]) if len(df) > 1 else wti_now
    chg = wti_now - wti_prev

    # Scoreboard from the stored prediction log.
    rows = db.read_predictions(limit=250)
    resolved = [r for r in rows if r["outcome"] in ("hit", "miss")]
    stands = [r for r in rows if r["outcome"] == "stand"]
    hits = [r for r in resolved if r["outcome"] == "hit"]
    live_acc = (len(hits) / len(resolved)) if resolved else None

    # 30 rows: about six weeks of trading days. Enough that the pattern of
    # hits, misses and stand-downs is visible at a glance rather than a
    # flattering slice. Raise LOG_ROWS if you want a longer tail.
    log = []
    for r in rows[:LOG_ROWS]:
        log.append({
            "date": r["date"],
            "call": r["state"],
            "prob": round(r["probability"], 3),
            "outcome": r["outcome"] or "open",
        })

    ev = evaluation or {}
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataThrough": str(last_date.date()),
        "isLive": True,
        "version": "v1B",

        # --- the honest limitation, carried in the data itself so the app
        # --- can display it without anyone having to remember to. ---------
        "limitation": (
            "Free EIA and FRED data publishes end-of-day with a lag, so in this "
            "local-script version a call is resolved the NEXT morning rather than "
            "live at the 2:30 PM ET settlement. The intraday tracking strip stays "
            "empty until a live price feed arrives in v1C."
        ),

        "asOf": f"Data through {last_date.strftime('%a %b %d')} \u00b7 locked 8:00 AM ET",
        "spot": {
            "last": round(wti_now, 2),
            "chg": round(chg, 2),
            "chgPct": round((chg / wti_prev * 100) if wti_prev else 0.0, 2),
        },
        "prediction": prediction,
        "briefing": _briefing(df, prediction, evaluation),
        "events": _events(last_date),
        "chart1D": [round(float(v), 2) for v in df["wti"].tail(30).tolist()],
        "chart5D": [round(float(v), 2) for v in df["wti"].tail(90).tolist()],
        "scoreboard": {
            "windowLabel": "Walk-forward validation (out of sample)",
            "fired": ev.get("n_fired", 0),
            "standDowns": ev.get("n_stood_down", 0),
            "hits": len(hits),
            "accuracy": round(ev.get("accuracy_when_fired", 0.0) or 0.0, 4),
            "baseline": round(ev.get("baseline_up_rate", 0.0) or 0.0, 4),
            "baselineLabel": "always guess up",
            "liveAccuracy": round(live_acc, 4) if live_acc is not None else None,
            "liveResolved": len(resolved),
            "liveStands": len(stands),
            "brier": round(ev.get("brier", 0.0) or 0.0, 4),
            "brierBaseline": round(ev.get("brier_baseline", 0.0) or 0.0, 4),
            "beatsBaseline": ev.get("beats_baseline", False),
            "calibration": ev.get("calibration", []),
            "calibrationNote": _calibration_note(ev),
            "log": log,
        },
        "sources": [
            {"name": "FRED \u2014 WTI, Brent, dollar, natural gas", "status": f"Live \u00b7 through {last_date.date()}"},
            {"name": "EIA \u2014 crude and Cushing stocks", "status": "Live \u00b7 weekly"},
            {"name": "CFTC \u2014 managed-money positioning", "status": "Live \u00b7 weekly"},
            {"name": "Futures curve (term structure)", "status": "Not connected \u00b7 planned v1C"},
            {"name": "Intraday price feed", "status": "Not connected \u00b7 planned v1C"},
            {"name": "News scoring", "status": "Not connected \u00b7 planned v1C"},
        ],
        "model": model_meta,
    }
    return payload


def _calibration_note(ev):
    cal = ev.get("calibration") or []
    if not cal:
        return "Not enough out-of-sample days yet to check calibration."
    worst = max(cal, key=lambda c: abs(c["predicted"] - c["actual"]))
    return (
        f"Of days the model put {worst['band']} on a direction, "
        f"{worst['actual']*100:.0f}% resolved that way against {worst['predicted']*100:.0f}% predicted "
        f"({worst['n']} days). Closer together is better."
    )


def _briefing(df, prediction, evaluation):
    """A briefing assembled from what the data actually says.

    Deliberately mechanical. v1C can write this with a language model; doing
    it from templates now means the app never invents a narrative the data
    does not support.
    """
    last = df.iloc[-1]
    ret5 = float(df["wti"].pct_change(5).iloc[-1] or 0) * 100
    vol20 = float(df["vol_20"].iloc[-1] or 0) * 100
    bw = float(last.get("brent_wti", 0) or 0)
    mm = float(last.get("mm_net_pct", 0.5) or 0.5)

    trend_word = "firmer" if ret5 > 1 else "softer" if ret5 < -1 else "little changed"
    vol_word = "elevated" if vol20 > 2.2 else "subdued" if vol20 < 1.2 else "ordinary"
    crowd_word = (
        "crowded long, which caps upside and raises unwind risk"
        if mm > 0.75 else
        "unusually light, which leaves room for buying"
        if mm < 0.25 else
        "middling, neither crowded nor cleared out"
    )

    state = prediction.get("state")
    lean_line = {
        "up": "The model leans to an up close today.",
        "down": "The model leans to a down close today.",
        "none": "The model is standing down today: the signals sit too close to even to be worth a call.",
    }[state]

    return {
        "headline": {
            "up": "Leaning up",
            "down": "Leaning down",
            "none": "No edge today",
        }[state],
        "paragraphs": [
            f"WTI is {trend_word} over the past week, {ret5:+.1f}% across five sessions, with realized "
            f"volatility {vol_word} at {vol20:.1f}% daily. {lean_line}",
            f"Brent trades ${bw:+.2f} against WTI. Managed-money positioning is {crowd_word} "
            f"(the {mm*100:.0f}th percentile of the last three years).",
            "This briefing is assembled directly from the day's feature values \u2014 it says what the "
            "inputs say and nothing more. Narrative summaries of news arrive in v1C.",
        ],
        "watching": _watch_list(),
    }


def _events(last_date):
    """The recurring release calendar, projected forward from the last data date.

    These cadences are fixed by the agencies: EIA Wednesday 10:30 ET, API
    Tuesday 16:30, CFTC Friday 15:30, Baker Hughes Friday 13:00. Holiday
    weeks shift them by a day; v1C should pull the actual published schedule.
    """
    import pandas as pd

    out = []
    recurring = [
        (1, "4:30 PM ET", "API weekly inventories", "medium",
         "Industry preview of Wednesday's EIA report. After-hours mover."),
        (2, "10:30 AM ET", "EIA Weekly Petroleum Status", "high",
         "The week's main event. The surprise against consensus moves price, not the level."),
        (4, "1:00 PM ET", "Baker Hughes rig count", "low",
         "Weekly US drilling activity. Slow-moving supply signal."),
        (4, "3:30 PM ET", "CFTC Commitments of Traders", "medium",
         "Managed-money positioning as of Tuesday. The crowding gauge."),
    ]
    start = last_date + pd.Timedelta(days=1)
    for offset in range(0, 14):
        day = start + pd.Timedelta(days=offset)
        for dow, time_s, name, impact, note in recurring:
            if day.dayofweek == dow:
                out.append({
                    "day": day.strftime("%a"),
                    "date": day.strftime("%b %d"),
                    "time": time_s,
                    "name": name,
                    "impact": impact,
                    "note": note,
                })
        if len(out) >= 8:
            break
    return out


def _watch_list():
    return [
        "EIA Weekly Petroleum Status, Wednesdays 10:30 AM ET",
        "CFTC Commitments of Traders, Fridays 3:30 PM ET",
        "OPEC+ headlines \u2014 unscheduled and the largest single risk",
    ]


def write(payload, path=None):
    path = path or config.EXPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path
