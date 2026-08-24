"""Fit the win-probability model on data/train and commit the artefact.

Discipline, in one paragraph, because it is the whole point of this file:
fitting happens on `data/train` and nothing else. The held-out split in
`data/test` is not read here, not peeked at to choose a hyperparameter, and not
used to pick the calibrator. The calibration method is selected by out-of-fold
cross-validation *inside* the training set. Any number reported after this
script runs on data the model has already seen is a training number and is
labelled as such.

The script also derives the escalation margin used by the EV engine. That
margin was a chosen constant (0.08); after fitting it becomes a measurement -
the model's expected calibration error, rounded up. The reading stays the same:
"our estimate would have to be wrong by less than its own typical error for
this call to flip", except now the typical error is known rather than guessed.

Usage:  python scripts/fit_win_model.py [--l2 1.0] [--folds 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vakil.decide.fit import (  # noqa: E402
    IdentityScaler,
    brier,
    cross_validate_calibration,
    expected_calibration_error,
    fit_isotonic,
    fit_logistic,
    fit_platt,
    log_loss,
)
from vakil.decide.win import (  # noqa: E402
    FEATURE_NAMES,
    WinModel,
    extract_features,
    feature_vector,
)
from vakil.ingest.corpus import load_split  # noqa: E402
from vakil.rules.ce3 import qualifies_ce3  # noqa: E402

TRAIN_DIR = ROOT / "data" / "train"
OUT_PATH = ROOT / "data" / "model" / "win_model.json"

#: Floor on the escalation margin. Even a perfectly calibrated model should not
#: auto-decide a case sitting two points from break-even; there is estimation
#: error the calibration curve cannot see, starting with the 8% label noise
#: baked into the corpus.
MIN_ESCALATION_MARGIN = 0.04


def build_training_set() -> tuple[list[tuple[float, ...]], list[int], list[str]]:
    cases = load_split(TRAIN_DIR)
    if not cases:
        raise SystemExit(f"no training cases in {TRAIN_DIR} - run `make data` first")

    rows, labels, reason_codes = [], [], []
    for case in cases:
        ce3 = qualifies_ce3(case.dispute, case.bundle, case.current)
        features = extract_features(case.bundle, ce3)
        rows.append(feature_vector(case.dispute.reason_code, features))
        labels.append(int(case.should_win))
        reason_codes.append(str(case.dispute.reason_code))
    return rows, labels, reason_codes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2", type=float, default=1.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    rows, labels, reason_codes = build_training_set()
    print(f"training rows   {len(rows)}  positives {sum(labels)}  features {len(FEATURE_NAMES)}")

    # --- choose a calibrator, out of fold -------------------------------
    cv = cross_validate_calibration(rows, labels, folds=args.folds, seed=args.seed, l2=args.l2)
    best = min(cv, key=lambda k: cv[k])
    print("\nout-of-fold Brier (lower is better)")
    for name, score in sorted(cv.items(), key=lambda kv: kv[1]):
        marker = "  <- chosen" if name == best else ""
        print(f"  {name:<9} {score:.4f}{marker}")

    # --- refit on the full training set ---------------------------------
    model_fit = fit_logistic(rows, labels, l2=args.l2)
    raw = [model_fit.predict(r) for r in rows]

    if best == "platt":
        scaler = fit_platt(raw, labels)
    elif best == "isotonic":
        scaler = fit_isotonic(raw, labels)
    else:
        scaler = IdentityScaler()

    calibrated = [scaler.apply(p) for p in raw]

    # --- derive the escalation margin from measured error ---------------
    ece = expected_calibration_error(calibrated, labels)
    margin = max(round(ece, 3), MIN_ESCALATION_MARGIN)

    coefficients = dict(zip(FEATURE_NAMES, model_fit.coefficients, strict=True))

    model = WinModel(
        intercept=model_fit.intercept,
        coefficients={k: round(v, 6) for k, v in coefficients.items()},
        calibration=scaler,
        source="fitted",
        metadata={
            "train_rows": len(rows),
            "train_positives": sum(labels),
            "l2": args.l2,
            "iterations": model_fit.iterations,
            "cv_folds": args.folds,
            "cv_brier_out_of_fold": {k: round(v, 4) for k, v in cv.items()},
            "calibration_chosen": best,
            "train_brier_raw": round(brier(raw, labels), 4),
            "train_brier_calibrated": round(brier(calibrated, labels), 4),
            "train_log_loss": round(log_loss(calibrated, labels), 4),
            "train_expected_calibration_error": round(ece, 4),
            "derived_escalation_margin": margin,
            "warning": (
                "Every train_* figure is measured on data the model was fitted on. "
                "Held-out numbers live in evals/report.md."
            ),
        },
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(model.to_dict(), indent=2), encoding="utf-8")

    print("\ntraining fit (in-sample, not a result)")
    print(f"  Brier raw        {brier(raw, labels):.4f}")
    print(f"  Brier calibrated {brier(calibrated, labels):.4f}")
    print(f"  ECE              {ece:.4f}")
    print(f"\nderived escalation margin  {margin:.3f}  (was a chosen 0.08)")
    print(f"written to {args.out.relative_to(ROOT)}")

    print("\nstrongest coefficients")
    ranked = sorted(coefficients.items(), key=lambda kv: -abs(kv[1]))
    for name, value in ranked[:8]:
        print(f"  {name:<26} {value:+.3f}")


if __name__ == "__main__":
    main()
