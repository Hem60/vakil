"""Fitting and calibration primitives.

Pure Python, no numpy, no scikit-learn. Two reasons, and neither is purity for
its own sake: 200 training rows by 15 features is small enough that a hand-
written gradient descent runs in well under a second, and a fitted model whose
coefficients are produced by code a reviewer can read end to end is easier to
defend to a panel than one that appears out of a library call.

Everything here is deterministic. Fold assignment is seeded, iteration counts
are fixed, and there is no early stopping on a random condition - so the same
training data always produces the same model, and `ledger.replay()` stays exact
across a refit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

Vector = Sequence[float]
Matrix = Sequence[Vector]

EPS = 1e-12

#: Decimal places kept when a fitted model is written to disk.
#:
#: Not cosmetic. CI refits the model and asserts the committed artefact is
#: unchanged, and raw float64 does not survive a change of platform: the same
#: training data produced a Platt intercept of 0.059706702901627724 on Windows
#: and 0.05970670290162777 on Linux - a gap of 5e-17, from a different libm
#: implementation of exp and log.
#:
#: Ten places is far more precision than any decision needs (the smallest
#: meaningful move in a probability here is around 1e-3) and far coarser than
#: last-bit noise, so the artefact becomes canonical without losing anything
#: that matters. A genuine change in the training data moves these parameters
#: by orders of magnitude more.
ARTIFACT_PRECISION = 10


def sigmoid(z: float) -> float:
    # Split by sign to avoid overflow in exp for large-magnitude logits.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


# ---------------------------------------------------------------------------
# Logistic regression
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogisticFit:
    intercept: float
    coefficients: tuple[float, ...]
    iterations: int
    l2: float
    final_log_loss: float

    def predict(self, x: Vector) -> float:
        z = self.intercept + sum(c * v for c, v in zip(self.coefficients, x, strict=True))
        return sigmoid(z)


def fit_logistic(
    features: Matrix,
    labels: Sequence[int],
    *,
    l2: float = 1.0,
    iterations: int = 4000,
    learning_rate: float = 0.35,
) -> LogisticFit:
    """Full-batch gradient descent on the L2-penalised log-likelihood.

    L2 matters more than it looks. The reason-code indicators are collinear
    with the intercept by construction, and several features are rare in 200
    rows; without a penalty the coefficients on those run away to fit a handful
    of cases, which is precisely the overfitting the held-out split exists to
    catch. The penalty deliberately does not touch the intercept - shrinking
    the base rate toward zero would be shrinking toward "always lose".
    """
    n = len(features)
    if n == 0:
        raise ValueError("no training rows")
    width = len(features[0])

    intercept = 0.0
    coefficients = [0.0] * width

    for _ in range(iterations):
        grad_intercept = 0.0
        grad = [0.0] * width

        for x, y in zip(features, labels, strict=True):
            z = intercept + sum(c * v for c, v in zip(coefficients, x, strict=True))
            error = sigmoid(z) - y
            grad_intercept += error
            for j, v in enumerate(x):
                grad[j] += error * v

        intercept -= learning_rate * grad_intercept / n
        for j in range(width):
            coefficients[j] -= learning_rate * (grad[j] / n + l2 * coefficients[j] / n)

    return LogisticFit(
        intercept=intercept,
        coefficients=tuple(coefficients),
        iterations=iterations,
        l2=l2,
        final_log_loss=log_loss(
            [sigmoid(intercept + sum(c * v for c, v in zip(coefficients, x, strict=True)))
             for x in features],
            labels,
        ),
    )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlattScaler:
    """A one-dimensional logistic on the logit. Two parameters, so it cannot
    do much damage on small data - it can stretch and shift the probability
    scale but not reorder anything."""

    a: float
    b: float

    def apply(self, p: float) -> float:
        return sigmoid(self.a * logit(p) + self.b)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": "platt",
            "a": round(self.a, ARTIFACT_PRECISION),
            "b": round(self.b, ARTIFACT_PRECISION),
        }


@dataclass(frozen=True)
class IsotonicScaler:
    """Pool-adjacent-violators. Non-parametric and far more flexible than
    Platt, which is both the appeal and the risk: on 200 rows with 8% label
    noise it will happily carve steps around individual cases. Which one is
    actually used is decided by cross-validation, not by preference."""

    #: (raw probability, calibrated probability), ascending by raw.
    breakpoints: tuple[tuple[float, float], ...]

    def apply(self, p: float) -> float:
        points = self.breakpoints
        if not points:
            return p
        if p <= points[0][0]:
            return points[0][1]
        if p >= points[-1][0]:
            return points[-1][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            if x0 <= p <= x1:
                if x1 - x0 < EPS:
                    return y1
                weight = (p - x0) / (x1 - x0)
                return y0 + weight * (y1 - y0)
        return points[-1][1]

    def to_dict(self) -> dict[str, object]:
        return {
            "method": "isotonic",
            "breakpoints": [
                [round(x, ARTIFACT_PRECISION), round(y, ARTIFACT_PRECISION)]
                for x, y in self.breakpoints
            ],
        }


@dataclass(frozen=True)
class IdentityScaler:
    def apply(self, p: float) -> float:
        return p

    def to_dict(self) -> dict[str, object]:
        return {"method": "none"}


Scaler = PlattScaler | IsotonicScaler | IdentityScaler


def fit_platt(probabilities: Sequence[float], labels: Sequence[int]) -> PlattScaler:
    rows = [[logit(p)] for p in probabilities]
    fit = fit_logistic(rows, labels, l2=0.0, iterations=2500, learning_rate=0.5)
    return PlattScaler(a=fit.coefficients[0], b=fit.intercept)


def fit_isotonic(probabilities: Sequence[float], labels: Sequence[int]) -> IsotonicScaler:
    """Pool-adjacent-violators, weighted by block size."""
    pairs = sorted(zip(probabilities, labels, strict=True))
    blocks: list[list[float]] = []  # [sum_y, count, sum_x]
    for x, y in pairs:
        blocks.append([float(y), 1.0, x])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            last = blocks.pop()
            prev = blocks.pop()
            blocks.append([prev[0] + last[0], prev[1] + last[1], prev[2] + last[2]])

    points: list[tuple[float, float]] = []
    for sum_y, count, sum_x in blocks:
        points.append((sum_x / count, sum_y / count))
    return IsotonicScaler(breakpoints=tuple(points))


def scaler_from_dict(payload: dict[str, Any]) -> Scaler:
    method = payload.get("method", "none")
    if method == "platt":
        return PlattScaler(a=float(payload["a"]), b=float(payload["b"]))
    if method == "isotonic":
        return IsotonicScaler(
            breakpoints=tuple((float(a), float(b)) for a, b in payload["breakpoints"])
        )
    return IdentityScaler()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def brier(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, labels, strict=True)) / len(labels)


def log_loss(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    total = 0.0
    for p, y in zip(probabilities, labels, strict=True):
        q = min(max(p, 1e-9), 1 - 1e-9)
        total += -(y * math.log(q) + (1 - y) * math.log(1 - q))
    return total / len(labels)


def expected_calibration_error(
    probabilities: Sequence[float], labels: Sequence[int], bins: int = 10
) -> float:
    """Average |predicted - observed| across bins, weighted by bin population.

    This is the number the escalation margin is derived from: it is, roughly,
    how wrong the model's stated probability tends to be. A decision whose
    margin over break-even is smaller than that is a decision the model is not
    entitled to make on its own.
    """
    total = 0.0
    n = len(labels)
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        members = [
            (p, y)
            for p, y in zip(probabilities, labels, strict=True)
            if lo <= p < hi or (i == bins - 1 and p >= hi)
        ]
        if not members:
            continue
        predicted = sum(p for p, _ in members) / len(members)
        observed = sum(y for _, y in members) / len(members)
        total += (len(members) / n) * abs(predicted - observed)
    return total


# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------


def k_fold_indices(n: int, folds: int, seed: int) -> list[list[int]]:
    import random

    order = list(range(n))
    random.Random(seed).shuffle(order)
    return [order[i::folds] for i in range(folds)]


def cross_validate_calibration(
    features: Matrix,
    labels: Sequence[int],
    *,
    folds: int = 5,
    seed: int = 20260824,
    l2: float = 1.0,
) -> dict[str, float]:
    """Compare raw, Platt and isotonic by out-of-fold Brier score.

    Choosing a calibrator by how well it fits the data it was fitted on would
    always pick isotonic, and would always be wrong. Every number here is
    out-of-fold.
    """
    partitions = k_fold_indices(len(labels), folds, seed)
    scores: dict[str, list[float]] = {"none": [], "platt": [], "isotonic": []}

    for held_out in partitions:
        held = set(held_out)
        train_idx = [i for i in range(len(labels)) if i not in held]
        if not train_idx or not held_out:
            continue

        train_x = [features[i] for i in train_idx]
        train_y = [labels[i] for i in train_idx]
        test_x = [features[i] for i in held_out]
        test_y = [labels[i] for i in held_out]

        model = fit_logistic(train_x, train_y, l2=l2)
        train_p = [model.predict(x) for x in train_x]
        test_p = [model.predict(x) for x in test_x]

        scores["none"].append(brier(test_p, test_y))
        platt = fit_platt(train_p, train_y)
        scores["platt"].append(brier([platt.apply(p) for p in test_p], test_y))
        isotonic = fit_isotonic(train_p, train_y)
        scores["isotonic"].append(brier([isotonic.apply(p) for p in test_p], test_y))

    return {name: sum(values) / len(values) for name, values in scores.items() if values}
