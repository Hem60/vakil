"""Win-model tests.

The fitted artefact and the code that consumes it are versioned separately -
one is JSON in `data/model`, the other is Python. Most of what can go wrong
here is the two drifting apart silently, so that is what these pin down.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vakil.decide.fit import IdentityScaler, PlattScaler
from vakil.decide.win import (
    EVIDENCE_FEATURES,
    FEATURE_NAMES,
    REASON_FEATURES,
    WinFeatures,
    WinModel,
    active_model,
    feature_vector,
    prior_model,
)
from vakil.models import ReasonCode

MODEL_PATH = Path("data/model/win_model.json")


def features(**overrides: object) -> WinFeatures:
    base: dict[str, object] = {
        "ce3_qualified": False,
        "signed_delivery_proof": False,
        "delivery_proof_unsigned": False,
        "address_match": False,
        "device_match": False,
        "support_thread_present": False,
        "policy_available": False,
        "refund_already_issued": False,
        "evidence_completeness": 0.0,
    }
    base.update(overrides)
    return WinFeatures(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ feature space


def test_feature_vector_matches_feature_names_length():
    row = feature_vector(ReasonCode.NON_DELIVERY, features())
    assert len(row) == len(FEATURE_NAMES)


def test_reason_code_is_one_hot():
    row = feature_vector(ReasonCode.NON_DELIVERY, features())
    indicators = row[len(EVIDENCE_FEATURES) :]
    assert sum(indicators) == 1.0
    assert len(indicators) == len(REASON_FEATURES)


def test_every_reason_code_has_an_indicator():
    for code in ReasonCode:
        row = feature_vector(code, features())
        assert sum(row[len(EVIDENCE_FEATURES) :]) == 1.0


def test_evidence_features_appear_in_declared_order():
    row = feature_vector(ReasonCode.NON_DELIVERY, features(ce3_qualified=True))
    assert row[EVIDENCE_FEATURES.index("ce3_qualified")] == 1.0


# ------------------------------------------------------------- persistence


def test_model_round_trips_through_dict():
    model = WinModel(
        intercept=-0.5,
        coefficients={name: 0.1 for name in FEATURE_NAMES},
        calibration=PlattScaler(a=1.2, b=-0.3),
        source="fitted",
    )
    restored = WinModel.from_dict(model.to_dict())
    f = features(ce3_qualified=True)
    assert restored.probability(ReasonCode.NON_DELIVERY, f) == pytest.approx(
        model.probability(ReasonCode.NON_DELIVERY, f)
    )


def test_loading_a_model_trained_on_different_features_is_refused():
    """Silently loading mismatched coefficients would apply the wrong weight to
    every feature - wrong answers rather than an error, which is worse."""
    payload = WinModel(intercept=0.0, coefficients={}).to_dict()
    payload["feature_names"] = ["something", "else"]
    with pytest.raises(ValueError, match="different feature set"):
        WinModel.from_dict(payload)


def test_missing_artefact_falls_back_to_the_prior(tmp_path: Path):
    model = active_model.__wrapped__(tmp_path / "absent.json")
    assert model.source == "prior"


def test_fallback_is_visible_not_silent():
    """`source` travels into the ledger and the eval report, so a run that used
    the unfitted prior can be identified after the fact."""
    assert prior_model().source == "prior"
    assert "source" in prior_model().to_dict()


# ------------------------------------------------------------- predictions


def test_ce3_qualification_raises_the_estimate():
    model = active_model()
    without = model.probability(ReasonCode.FRAUD_CARD_ABSENT, features())
    with_ce3 = model.probability(ReasonCode.FRAUD_CARD_ABSENT, features(ce3_qualified=True))
    assert with_ce3 > without


def test_probabilities_stay_in_range():
    model = active_model()
    for code in ReasonCode:
        for f in (features(), features(ce3_qualified=True, evidence_completeness=1.0)):
            p = model.probability(code, f)
            assert 0.0 <= p <= 1.0


def test_contributions_omit_features_that_did_not_apply():
    """A list of things that were not true is noise, not an explanation."""
    model = active_model()
    contributions = model.contributions(ReasonCode.NON_DELIVERY, features())
    assert "ce3_qualified" not in contributions
    assert all(abs(v) > 0 for v in contributions.values())


def test_contributions_include_the_reason_code_indicator():
    model = active_model()
    contributions = model.contributions(ReasonCode.NOT_AS_DESCRIBED, features())
    assert "rc_13_3" in contributions


def test_calibration_is_applied_on_top_of_the_raw_score():
    model = WinModel(
        intercept=0.0,
        coefficients=dict.fromkeys(FEATURE_NAMES, 0.0),
        calibration=PlattScaler(a=1.0, b=2.0),
    )
    f = features()
    assert model.raw_probability(ReasonCode.NON_DELIVERY, f) == pytest.approx(0.5)
    assert model.probability(ReasonCode.NON_DELIVERY, f) > 0.8


def test_identity_calibration_leaves_the_raw_score_alone():
    model = WinModel(
        intercept=0.4,
        coefficients=dict.fromkeys(FEATURE_NAMES, 0.0),
        calibration=IdentityScaler(),
    )
    f = features()
    assert model.probability(ReasonCode.NON_DELIVERY, f) == pytest.approx(
        model.raw_probability(ReasonCode.NON_DELIVERY, f)
    )


# --------------------------------------------------- the committed artefact


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="model not fitted yet")
def test_committed_model_is_fitted_and_carries_its_provenance():
    payload = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert payload["source"] == "fitted"
    meta = payload["metadata"]
    assert meta["train_rows"] > 0
    assert meta["calibration_chosen"] in {"none", "platt", "isotonic"}
    assert "cv_brier_out_of_fold" in meta


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="model not fitted yet")
def test_committed_model_declares_its_escalation_margin():
    """The EV engine reads this to decide when to abstain. Without it the
    engine silently falls back to a hard-coded default."""
    meta = json.loads(MODEL_PATH.read_text(encoding="utf-8"))["metadata"]
    margin = meta["derived_escalation_margin"]
    assert 0.0 < margin < 0.5


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="model not fitted yet")
def test_ce3_remains_the_strongest_evidence_signal():
    """Not a golden number - a sanity check on the domain. If fitting ever
    decides a prior relationship does not matter for card-absent fraud, the
    corpus or the features have broken, not Visa's rulebook."""
    model = active_model()
    evidence_weights = {
        name: abs(model.coefficients.get(name, 0.0)) for name in EVIDENCE_FEATURES
    }
    assert max(evidence_weights, key=lambda k: evidence_weights[k]) == "ce3_qualified"


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="model not fitted yet")
def test_every_persisted_float_is_rounded_to_artefact_precision():
    """CI refits the model and asserts the committed artefact is byte-identical.
    Raw float64 does not survive a change of platform - the same training data
    gave a Platt intercept of 0.059706702901627724 on Windows and
    0.05970670290162777 on Linux. Rounding every persisted float makes the
    artefact canonical, so the staleness guard tests the training data rather
    than the C library.
    """
    from vakil.decide.fit import ARTIFACT_PRECISION

    payload = json.loads(MODEL_PATH.read_text(encoding="utf-8"))

    def decimals(value: float) -> int:
        text = repr(float(value))
        return len(text.split(".")[1]) if "." in text and "e" not in text else 0

    floats: list[tuple[str, float]] = [("intercept", payload["intercept"])]
    floats += [(f"coefficients.{k}", v) for k, v in payload["coefficients"].items()]
    calibration = payload["calibration"]
    floats += [
        (f"calibration.{k}", v)
        for k, v in calibration.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    for i, point in enumerate(calibration.get("breakpoints", [])):
        floats += [(f"calibration.breakpoints[{i}][{j}]", v) for j, v in enumerate(point)]

    too_precise = [(name, v) for name, v in floats if decimals(v) > ARTIFACT_PRECISION]
    assert not too_precise, f"unrounded floats will break CI across platforms: {too_precise}"
