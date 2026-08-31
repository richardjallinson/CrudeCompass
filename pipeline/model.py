"""Crude Compass v1B — model.

Logistic regression, standardized inputs, L2 regularization. Deliberately
simple: on a few thousand daily rows with a signal this thin, a gradient
booster or an LSTM will fit the noise beautifully and tell you nothing. A
linear model also has coefficients you can read, which is what feeds the
app's "Why" list — an unexplainable lean is not decision support.

The validation here is the point of the whole version. `walk_forward` never
lets the model see a day before predicting it: train on everything up to a
cut, predict the next block, step, refit. That number — accuracy against
the always-up baseline — decides whether the Today card ships at all.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

import config


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.05, max_iter=2000, solver="lbfgs"),
    )


def walk_forward(X, y, min_train=None, step=None):
    """Out-of-sample probabilities for every day after the first training block.

    Returns (probs, mask) where mask marks which rows got a prediction.
    """
    min_train = min_train or config.WALKFORWARD_MIN_TRAIN
    step = step or config.WALKFORWARD_STEP

    n = len(y)
    probs = np.full(n, np.nan)
    if n <= min_train + step:
        return probs, np.zeros(n, dtype=bool)

    start = min_train
    while start < n:
        end = min(start + step, n)
        model = make_model()
        model.fit(X[:start], y[:start])
        probs[start:end] = model.predict_proba(X[start:end])[:, 1]
        start = end

    return probs, ~np.isnan(probs)


def calibrate_walk_forward(probs, mask, y, min_fit=200, step=None):
    """Out-of-sample CALIBRATED probabilities, same discipline as walk_forward.

    The app displays a calibrated percentage, so the scoreboard has to grade a
    calibrated percentage or it is grading a number nobody sees. But fitting
    the calibrator on all of history and then scoring it on that same history
    would flatter it: isotonic regression can bend itself onto any sample it
    is shown. So this walks forward too — each block is calibrated by a map
    fitted only on the out-of-sample days BEFORE it.

    Returns (cal_probs, cal_mask). The first `min_fit` out-of-sample days have
    no calibrator yet and are left out rather than silently shown raw.
    """
    step = step or config.WALKFORWARD_STEP
    p = probs[mask]
    truth = y[mask]

    cal = np.full(len(p), np.nan)
    start = min_fit
    while start < len(p):
        end = min(start + step, len(p))
        calibrator = fit_calibrator(p[:start], truth[:start])
        if calibrator is not None:
            cal[start:end] = calibrator.predict(p[start:end])
        start = end

    out = np.full(len(probs), np.nan)
    out[mask] = cal
    return out, ~np.isnan(out)


def evaluate(probs, y, mask, display_probs=None, display_mask=None):
    """The honest scorecard.

    `probs` are the model's raw scores and always drive the UP/DOWN/NO EDGE
    decision — the accuracy-when-fired figure was measured against raw
    thresholds, and re-deciding on calibrated numbers would invalidate it.

    `display_probs`, when supplied, are what the app actually puts on screen.
    The Brier score and the calibration table are computed from those, so the
    scoreboard grades the number the user sees rather than an internal one.
    """
    p = probs[mask]
    truth = y[mask]
    if len(p) == 0:
        return None

    fired = (p <= config.STAND_DOWN_LOW) | (p >= config.STAND_DOWN_HIGH)
    calls = (p >= 0.5).astype(int)

    baseline = float(truth.mean())  # always-guess-up accuracy
    overall = float((calls == truth).mean())
    fired_acc = float((calls[fired] == truth[fired]).mean()) if fired.sum() else float("nan")

    # Which probabilities get GRADED. Decisions above came from the raw
    # scores; the numbers below are the ones shown on screen, when we have
    # them. They can cover fewer days, because the earliest out-of-sample
    # days predate any calibrator.
    if display_probs is not None and display_mask is not None and display_mask.sum() > 0:
        shown = display_probs[display_mask]
        shown_truth = y[display_mask]
        shown_is_calibrated = True
    else:
        shown, shown_truth = p, truth
        shown_is_calibrated = False

    # Brier score: mean squared error of the probability itself. Lower is
    # better; the always-say-baseline model scores baseline*(1-baseline).
    brier = float(np.mean((shown - shown_truth) ** 2))
    brier_ref = float(baseline * (1 - baseline))

    # Calibration: within each probability bucket, how often did it happen?
    # Fixed bands across the FULL probability range. The leading (0, 0.40)
    # band matters: without it, every day the model was confident of a DOWN
    # move (which is exactly the mirror of the 60-101% band on the up side)
    # would silently vanish from this table. A calibration check that only
    # covers one direction isn't a calibration check.
    bins = [(0.0, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 1.01)]
    calibration = []
    for lo, hi in bins:
        sel = (shown >= lo) & (shown < hi)
        if sel.sum() >= 10:
            calibration.append({
                "band": f"{int(lo*100)}-{int(hi*100)}%",
                "n": int(sel.sum()),
                "predicted": float(shown[sel].mean()),
                "actual": float(shown_truth[sel].mean()),
            })

    return {
        "n_out_of_sample": int(mask.sum()),
        "baseline_up_rate": baseline,
        "accuracy_all_days": overall,
        "accuracy_when_fired": fired_acc,
        "n_fired": int(fired.sum()),
        "n_stood_down": int((~fired).sum()),
        "brier": brier,
        "brier_baseline": brier_ref,
        "n_scored": int(len(shown)),
        "scored_calibrated": shown_is_calibrated,
        "beats_baseline": bool(fired_acc == fired_acc and fired_acc > baseline),
        "calibration": calibration,
    }


def fit_final(X, y):
    model = make_model()
    model.fit(X, y)
    return model


def predict_one(model, x_row, cols):
    """Probability plus the contributions that produced it.

    Contribution = standardized feature value * coefficient. That is exactly
    what moved the log-odds, so the app's "Why" list is the model's actual
    reasoning rather than a story told afterwards.
    """
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]

    x = np.asarray(x_row, dtype=float).reshape(1, -1)
    z = scaler.transform(x)[0]
    coefs = clf.coef_[0]
    contribs = z * coefs

    prob = float(clf.predict_proba(scaler.transform(x))[0, 1])
    ranked = sorted(
        [(cols[i], float(contribs[i])) for i in range(len(cols))],
        key=lambda t: abs(t[1]),
        reverse=True,
    )
    return prob, ranked


def state_for(prob):
    if prob >= config.STAND_DOWN_HIGH:
        return "up"
    if prob <= config.STAND_DOWN_LOW:
        return "down"
    return "none"


def fit_calibrator(probs, y):
    """Maps a raw probability onto what actually happens, via isotonic
    regression. Isotonic only assumes the mapping is monotonic (higher raw
    score -> higher true rate), which is the right amount of assumption for
    this: we are not claiming to know the SHAPE of the model's overconfidence,
    only that it has a consistent direction.

    Needs a real sample to avoid the calibration curve itself overfitting;
    below that, returns None and callers should show the raw probability.
    """
    if len(probs) < 200:
        return None
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(probs, y)
    return iso


def calibrate(calibrator, prob):
    """Raw probability in, honest probability out. Falls back to raw if
    there is no calibrator (not enough history yet)."""
    if calibrator is None:
        return float(prob)
    return float(calibrator.predict([prob])[0])
