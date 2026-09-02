"""Crude Compass v1C — model.

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


def calibrate_walk_forward(probs, y, mask, min_train=None, step=None, min_days=None):
    """Calibrated out-of-sample probabilities, with no look-ahead.

    The Scoreboard must grade the SAME numbers the app displays, and the app
    displays calibrated probabilities. Grading raw walk-forward output would
    be grading a number nobody sees.

    So: walk the same blocks walk_forward() used. For each block, fit an
    isotonic map on the out-of-sample days BEFORE that block only, then
    apply it to the block. Early blocks, before enough history exists to
    fit a calibrator, are left NaN and stay ungraded. That is why the
    scored-day count is lower than the out-of-sample count.

    Returns (cal_probs, cal_mask).
    """
    min_train = min_train or config.WALKFORWARD_MIN_TRAIN
    step = step or config.WALKFORWARD_STEP
    min_days = min_days or config.CALIBRATION_MIN_DAYS

    n = len(y)
    cal = np.full(n, np.nan)
    if not mask.any():
        return cal, np.zeros(n, dtype=bool)

    start = min_train
    while start < n:
        end = min(start + step, n)
        prior = mask.copy()
        prior[start:] = False
        if prior.sum() >= min_days:
            calibrator = fit_calibrator(probs[prior], y[prior])
            block = mask[start:end]
            if calibrator is not None and block.any():
                seg = probs[start:end]
                cal[start:end] = np.where(block, calibrator.predict(np.nan_to_num(seg, nan=0.5)), np.nan)
        start = end

    return cal, ~np.isnan(cal)


def evaluate(probs, y, mask, cal_probs=None, cal_mask=None):
    """The honest scorecard.

    Direction (fired / stood down / hit / miss) is judged on the RAW score,
    because that is what decides whether a call fires and the 53%-vs-baseline
    result was measured that way. The probability itself (Brier score and
    the calibration table) is judged on the CALIBRATED number when one is
    available, because that is what the dial shows.
    """
    p = probs[mask]
    truth = y[mask]
    if len(p) == 0:
        return None

    scored_calibrated = cal_probs is not None and cal_mask is not None and cal_mask.any()
    if scored_calibrated:
        q = cal_probs[cal_mask]
        q_truth = y[cal_mask]
    else:
        q, q_truth = p, truth

    fired = (p <= config.STAND_DOWN_LOW) | (p >= config.STAND_DOWN_HIGH)
    calls = (p >= 0.5).astype(int)

    baseline = float(truth.mean())  # always-guess-up accuracy
    overall = float((calls == truth).mean())
    fired_acc = float((calls[fired] == truth[fired]).mean()) if fired.sum() else float("nan")

    # Brier score: mean squared error of the probability itself. Lower is
    # better; the always-say-baseline model scores baseline*(1-baseline).
    brier = float(np.mean((q - q_truth) ** 2))
    q_base = float(q_truth.mean())
    brier_ref = float(q_base * (1 - q_base))

    # Calibration: within each probability bucket, how often did it happen?
    # Fixed bands across the FULL probability range. The leading (0, 0.40)
    # band matters: without it, every day the model was confident of a DOWN
    # move (which is exactly the mirror of the 60-101% band on the up side)
    # would silently vanish from this table. A calibration check that only
    # covers one direction isn't a calibration check.
    bins = [(0.0, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 1.01)]
    calibration = []
    for lo, hi in bins:
        sel = (q >= lo) & (q < hi)
        if sel.sum() >= 10:
            calibration.append({
                "band": f"{int(lo*100)}-{int(hi*100)}%",
                "n": int(sel.sum()),
                "predicted": float(q[sel].mean()),
                "actual": float(q_truth[sel].mean()),
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
        "beats_baseline": bool(fired_acc == fired_acc and fired_acc > baseline),
        "calibration": calibration,
        "scored_calibrated": bool(scored_calibrated),
        "n_scored": int(len(q)),
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
    if len(probs) < config.CALIBRATION_MIN_DAYS:
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
