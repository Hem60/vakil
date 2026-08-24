"""Fitting and calibration tests.

The win model multiplies into every rupee figure the system produces, so the
machinery that produces it gets tested on properties rather than on golden
numbers - golden numbers would pin the implementation and tell us nothing about
whether it is right.
"""

from __future__ import annotations

import math

import pytest

from vakil.decide.fit import (
    IdentityScaler,
    IsotonicScaler,
    brier,
    cross_validate_calibration,
    expected_calibration_error,
    fit_isotonic,
    fit_logistic,
    fit_platt,
    k_fold_indices,
    log_loss,
    logit,
    scaler_from_dict,
    sigmoid,
)


def separable_dataset(n: int = 120) -> tuple[list[list[float]], list[int]]:
    """One feature that determines the label, one that is pure noise."""
    rows, labels = [], []
    for i in range(n):
        signal = 1.0 if i % 2 == 0 else 0.0
        noise = float((i * 7) % 3) / 2.0
        rows.append([signal, noise])
        labels.append(int(signal))
    return rows, labels


# ------------------------------------------------------------------ basics


def test_sigmoid_is_stable_at_extremes():
    """Naive 1/(1+exp(-z)) overflows on large negative z. A crash here would
    surface as a dead pipeline on an unusual case, not as a wrong number."""
    assert sigmoid(-1000.0) == pytest.approx(0.0, abs=1e-12)
    assert sigmoid(1000.0) == pytest.approx(1.0, abs=1e-12)
    assert sigmoid(0.0) == pytest.approx(0.5)


def test_logit_inverts_sigmoid():
    for p in (0.1, 0.35, 0.5, 0.87):
        assert sigmoid(logit(p)) == pytest.approx(p, abs=1e-6)


def test_logit_clamps_rather_than_diverging():
    assert math.isfinite(logit(0.0))
    assert math.isfinite(logit(1.0))


# ------------------------------------------------------- logistic regression


def test_fit_recovers_the_signal_and_ignores_the_noise():
    rows, labels = separable_dataset()
    fit = fit_logistic(rows, labels, l2=0.01)
    assert fit.coefficients[0] > 2.0
    assert abs(fit.coefficients[1]) < abs(fit.coefficients[0]) / 4


def test_l2_shrinks_coefficients():
    rows, labels = separable_dataset()
    weak = fit_logistic(rows, labels, l2=0.01)
    strong = fit_logistic(rows, labels, l2=50.0)
    assert abs(strong.coefficients[0]) < abs(weak.coefficients[0])


def test_fitting_is_deterministic():
    """Same training data must give the same model, or ledger replay stops
    being exact across a refit."""
    rows, labels = separable_dataset()
    a = fit_logistic(rows, labels)
    b = fit_logistic(rows, labels)
    assert a.coefficients == b.coefficients
    assert a.intercept == b.intercept


def test_fit_rejects_an_empty_training_set():
    with pytest.raises(ValueError, match="no training rows"):
        fit_logistic([], [])


def test_predictions_improve_on_the_base_rate():
    rows, labels = separable_dataset()
    fit = fit_logistic(rows, labels, l2=0.01)
    predictions = [fit.predict(r) for r in rows]
    base_rate = [sum(labels) / len(labels)] * len(labels)
    assert brier(predictions, labels) < brier(base_rate, labels)


# ------------------------------------------------------------- calibration


def test_isotonic_output_is_monotone():
    probabilities = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    labels = [0, 0, 1, 0, 1, 1, 0, 1]
    scaler = fit_isotonic(probabilities, labels)
    calibrated = [scaler.apply(p) for p in sorted(probabilities)]
    assert calibrated == sorted(calibrated)


def test_isotonic_interpolates_between_breakpoints():
    scaler = IsotonicScaler(breakpoints=((0.0, 0.0), (1.0, 1.0)))
    assert scaler.apply(0.25) == pytest.approx(0.25)


def test_isotonic_clamps_outside_its_range():
    scaler = IsotonicScaler(breakpoints=((0.2, 0.1), (0.8, 0.9)))
    assert scaler.apply(0.0) == pytest.approx(0.1)
    assert scaler.apply(1.0) == pytest.approx(0.9)


def test_platt_corrects_systematic_overconfidence():
    """A model that says 0.9 when the truth is 0.5 should be pulled down."""
    probabilities = [0.9] * 50 + [0.8] * 50
    labels = [1, 0] * 50  # exactly half win
    scaler = fit_platt(probabilities, labels)
    before = brier(probabilities, labels)
    after = brier([scaler.apply(p) for p in probabilities], labels)
    assert after < before
    assert scaler.apply(0.9) < 0.9


def test_identity_scaler_changes_nothing():
    assert IdentityScaler().apply(0.42) == 0.42


def test_scalers_round_trip_through_dict():
    for scaler in (
        IdentityScaler(),
        fit_platt([0.2, 0.8, 0.6, 0.4], [0, 1, 1, 0]),
        fit_isotonic([0.2, 0.4, 0.6, 0.8], [0, 0, 1, 1]),
    ):
        restored = scaler_from_dict(scaler.to_dict())
        assert restored.apply(0.55) == pytest.approx(scaler.apply(0.55))


def test_unknown_calibration_method_falls_back_to_identity():
    """A model artefact written by a future version must not silently apply a
    calibration this code does not understand."""
    assert scaler_from_dict({"method": "quantum"}).apply(0.3) == 0.3


# ----------------------------------------------------------------- scoring


def test_brier_is_zero_for_perfect_predictions():
    assert brier([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_log_loss_is_finite_for_confident_mistakes():
    """An unclamped log loss returns infinity on a confident miss, which would
    poison every average it appears in."""
    assert math.isfinite(log_loss([1.0], [0]))


def test_expected_calibration_error_is_zero_when_calibrated():
    probabilities = [0.25] * 100
    labels = [1] * 25 + [0] * 75
    assert expected_calibration_error(probabilities, labels) == pytest.approx(0.0, abs=1e-9)


def test_expected_calibration_error_detects_overconfidence():
    probabilities = [0.95] * 100
    labels = [1] * 50 + [0] * 50
    assert expected_calibration_error(probabilities, labels) == pytest.approx(0.45, abs=0.01)


# ------------------------------------------------------- cross-validation


def test_k_fold_partitions_cover_every_row_exactly_once():
    partitions = k_fold_indices(23, folds=5, seed=1)
    flat = sorted(i for part in partitions for i in part)
    assert flat == list(range(23))


def test_k_fold_is_seeded():
    assert k_fold_indices(50, 5, seed=7) == k_fold_indices(50, 5, seed=7)
    assert k_fold_indices(50, 5, seed=7) != k_fold_indices(50, 5, seed=8)


def test_cross_validation_scores_all_three_calibrators():
    rows, labels = separable_dataset()
    scores = cross_validate_calibration(rows, labels, folds=4)
    assert set(scores) == {"none", "platt", "isotonic"}
    assert all(0.0 <= v <= 1.0 for v in scores.values())
