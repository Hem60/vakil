"""End-to-end runner tests.

Every stage was individually tested before this module existed; none had ever
been run as one thing outside an ad-hoc script. These pin the seam - that the
stages hand off correctly, that stopping early is a normal outcome rather than
a failure, and that the whole thing runs with no API key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vakil.config import Settings
from vakil.ledger.chain import Ledger
from vakil.models import Verdict
from vakil.runner import run_case

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)

#: A case that qualifies for CE 3.0 and files cleanly - chosen because it
#: exercises every stage, not because it flatters the result.
FILES_CLEANLY = Path("data/test/case_0019.json")
#: Sits within the escalation margin of break-even.
ESCALATES = Path("data/test/case_0006.json")


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def require(path: Path) -> Path:
    if not path.exists():
        pytest.skip("corpus not generated - run make data")
    return path


def run(path: Path, ledger: Ledger, **kwargs: object):
    return run_case(require(path), Settings(), NOW, ledger=ledger, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------- the full path


def test_a_clean_case_reaches_under_review(ledger: Ledger):
    result = run(FILES_CLEANLY, ledger)

    assert result.verdict is Verdict.FIGHT
    assert result.letter is not None
    assert result.filing is not None
    assert result.filing.status == "under_review"
    assert not result.stopped


def test_the_whole_run_needs_no_api_key(ledger: Ledger):
    """The decision path is deterministic and the default drafter builds
    sentences from facts. A demo that needs credentials is a demo most people
    never see."""
    settings = Settings(anthropic_api_key="", gemini_api_key="")
    result = run_case(require(FILES_CLEANLY), settings, NOW, ledger=ledger)
    assert result.filing is not None


def test_every_stage_reaches_the_ledger(ledger: Ledger):
    run(FILES_CLEANLY, ledger)
    stages = [record["event"]["stage"] for record in ledger.records()]
    assert stages[:2] == ["decide", "draft"]
    assert "filed" in stages
    ok, _ = ledger.verify()
    assert ok


def test_the_letter_only_contains_verified_claims(ledger: Ledger):
    result = run(FILES_CLEANLY, ledger)
    assert result.letter is not None
    for claim in result.letter.stripped:
        assert claim.text not in result.letter.body()


# ------------------------------------------------- stopping early is normal


def test_a_case_that_does_not_reach_fight_stops_before_drafting(ledger: Ledger):
    """Most disputes should end this way. A runner that treated it as a failure
    would be arguing with its own decision engine."""
    result = run(ESCALATES, ledger)

    assert result.verdict is not Verdict.FIGHT
    assert result.letter is None
    assert result.filing is None
    assert str(result.verdict) in result.stopped


def test_an_expired_deadline_stops_at_the_filing_gate(ledger: Ledger):
    result = run_case(
        require(FILES_CLEANLY),
        Settings(),
        NOW + timedelta(days=400),
        ledger=ledger,
    )
    assert result.filing is None
    assert result.stopped


def test_no_file_stops_after_the_letter(ledger: Ledger):
    result = run(FILES_CLEANLY, ledger, should_file=False)
    assert result.letter is not None
    assert result.filing is None
    assert "--no-file" in result.stopped


def test_a_second_run_is_refused_by_the_ledger(ledger: Ledger):
    """Idempotency holds across the whole pipeline, not just inside filing."""
    run(FILES_CLEANLY, ledger)
    second = run(FILES_CLEANLY, ledger)
    assert second.filing is None
    assert "file twice" in second.stopped


# ------------------------------------------------------ withdrawing evidence


def test_withdrawing_the_courier_document_shortens_the_letter(ledger: Ledger, tmp_path: Path):
    """The provenance demo, through the full pipeline rather than the gate
    alone: same case, one document withdrawn, and the sentences that rested on
    it leave rather than being invented."""
    whole = run(FILES_CLEANLY, ledger)
    without = run(FILES_CLEANLY, Ledger(tmp_path / "second.jsonl"), drop="delivery")

    assert whole.letter is not None
    assert without.letter is not None
    assert len(without.letter.verified) < len(whole.letter.verified)
    assert "signed for by" in whole.letter.body()
    assert "signed for by" not in without.letter.body()


def test_withdrawing_evidence_does_not_invent_a_replacement(ledger: Ledger):
    """Nothing in the shortened letter may mention delivery at all."""
    result = run(FILES_CLEANLY, ledger, drop="delivery")
    assert result.letter is not None
    body = result.letter.body().lower()
    assert "delivered" not in body
    assert "tracking" not in body


def test_the_fact_index_shrinks_when_evidence_is_withdrawn(ledger: Ledger, tmp_path: Path):
    whole = run(FILES_CLEANLY, ledger)
    without = run(FILES_CLEANLY, Ledger(tmp_path / "b.jsonl"), drop="delivery")
    assert len(without.index.facts) < len(whole.index.facts)
    assert without.index.get("delivery.delivered_at") is None
