"""Circuit breaker and checkpointing tests.

Both exist because of one four-hour run that produced nothing. The free-tier
quota was exhausted, every request was held ~170 seconds before being refused,
the retry ladder tried each document three more times, and nothing was written
to disk until an end that never came.

So: a run that hits a wall must stop at the wall, and a run that dies must keep
what it finished. These tests pin both without touching a network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from extraction_eval import load_checkpoint, run  # noqa: E402

from vakil.evidence.extract import ExtractedField, ExtractedPOD, ExtractionResult
from vakil.evidence.gemini import GeminiError, GeminiUnavailable, _is_quota_exhausted


def entry(index: int, quality: str = "clean") -> dict:
    return {
        "case_id": f"case_{index:04d}",
        "document": f"fixtures/pod/{index:04d}.pdf",
        "quality": quality,
        "expected": {
            "tracking_id": "EKA1",
            "carrier": "Ekart",
            "delivered_at": "2026-08-19",
            "signed_by": None,
            "delivered_to_address": "12 MG Road",
        },
    }


def good_pod() -> ExtractedPOD:
    def f(value: str | None, legible: bool = True) -> ExtractedField:
        return ExtractedField(value=value, source_quote=value, legible=legible)

    return ExtractedPOD(
        tracking_id=f("EKA1"),
        carrier=f("Ekart"),
        delivered_at=f("2026-08-19"),
        signed_by=f(None),
        delivered_to_address=f("12 MG Road"),
    )


class ScriptedExtractor:
    """Succeeds or raises per call, following a script."""

    def __init__(self, script: list[Exception | None]) -> None:
        self.script = list(script)
        self.calls = 0

    def extract(self, document_path: Path) -> ExtractionResult:
        outcome = self.script[self.calls] if self.calls < len(self.script) else None
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return ExtractionResult(
            document_uri=str(document_path), extracted=good_pod(), model="scripted"
        )


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Ten fixture files that exist on disk, so path checks pass."""
    import extraction_eval

    pod_dir = tmp_path / "fixtures" / "pod"
    pod_dir.mkdir(parents=True)
    entries = [entry(i) for i in range(10)]
    for e in entries:
        (tmp_path / e["document"]).write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(extraction_eval, "DATA", tmp_path)
    return entries


# ------------------------------------------------------------ circuit breaker


def test_unavailable_api_stops_the_run_immediately(documents: list[dict]):
    """Quota exhaustion will not clear over the next 174 documents. The run
    that motivated this spent four hours proving that."""
    extractor = ScriptedExtractor([GeminiUnavailable("free-tier quota exhausted")])
    report = run(extractor, documents)

    assert extractor.calls == 1
    assert "quota exhausted" in report["aborted"]
    assert report["documents"] == 0
    assert report["remaining"] == 9


def test_run_stops_after_three_consecutive_failures(documents: list[dict]):
    extractor = ScriptedExtractor([GeminiError("HTTP 500")] * 10)
    report = run(extractor, documents, max_consecutive_failures=3)

    assert extractor.calls == 3
    assert "3 consecutive failures" in report["aborted"]


def test_isolated_failures_do_not_stop_the_run(documents: list[dict]):
    """One unreadable page is not a systemic failure. The breaker must not fire
    on noise, or it becomes a reason to disable it."""
    script: list[Exception | None] = [None] * 10
    script[2] = GeminiError("HTTP 400 bad page")
    script[6] = GeminiError("HTTP 400 bad page")
    report = run(ScriptedExtractor(script), documents, max_consecutive_failures=3)

    assert not report["aborted"]
    assert report["documents"] == 8
    assert len(report["failures"]) == 2


def test_the_failure_counter_resets_on_success(documents: list[dict]):
    """Two failures, a success, two more failures is not three in a row."""
    script: list[Exception | None] = [
        GeminiError("x"), GeminiError("x"), None, GeminiError("x"), GeminiError("x"), None,
    ] + [None] * 4
    report = run(ScriptedExtractor(script), documents, max_consecutive_failures=3)
    assert not report["aborted"]


# --------------------------------------------------------------- checkpoint


def test_completed_documents_are_written_as_they_finish(
    documents: list[dict], tmp_path: Path
):
    """The four-hour run wrote nothing until an end that never arrived."""
    checkpoint = tmp_path / "ck.jsonl"
    script: list[Exception | None] = [None, None, GeminiUnavailable("quota")]
    run(ScriptedExtractor(script), documents, checkpoint=checkpoint)

    saved = load_checkpoint(checkpoint)
    assert len(saved) == 2
    assert all("outcomes" in row for row in saved.values())


def test_a_resumed_run_does_not_re_send_finished_documents(
    documents: list[dict], tmp_path: Path
):
    checkpoint = tmp_path / "ck.jsonl"
    run(ScriptedExtractor([None, None, GeminiUnavailable("quota")]), documents,
        checkpoint=checkpoint)

    second = ScriptedExtractor([None] * 10)
    report = run(second, documents, checkpoint=checkpoint)

    assert second.calls == 8  # ten documents minus the two already done
    assert report["resumed_from_checkpoint"] == 2
    assert report["documents"] == 10


def test_resumed_rows_are_counted_in_the_totals(documents: list[dict], tmp_path: Path):
    """A resumed run reports the whole set, not just what it re-ran."""
    checkpoint = tmp_path / "ck.jsonl"
    run(ScriptedExtractor([None] * 4 + [GeminiUnavailable("quota")]), documents,
        checkpoint=checkpoint)
    report = run(ScriptedExtractor([None] * 10), documents, checkpoint=checkpoint)

    assert report["overall"]["fields_scored"] == 50  # 10 documents x 5 fields


def test_missing_checkpoint_is_not_an_error(tmp_path: Path):
    assert load_checkpoint(tmp_path / "absent.jsonl") == {}


def test_report_states_that_a_partial_run_is_partial(documents: list[dict], tmp_path: Path):
    """A partial run's numbers are a different claim from a complete run's, and
    the reader should not have to infer which they are looking at."""
    import extraction_eval

    report = run(
        ScriptedExtractor([None, None, GeminiUnavailable("quota")]),
        documents,
        checkpoint=tmp_path / "ck.jsonl",
    )
    rendered = extraction_eval.render(report, "gemini-3.6-flash")
    assert "Run aborted" in rendered
    assert "were not attempted" in rendered


# ------------------------------------------------------- quota classification


@pytest.mark.parametrize(
    "body",
    [
        "You exceeded your current quota, please check your plan",
        '{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota_exceeded"}}',
    ],
)
def test_quota_exhaustion_is_recognised(body: str):
    assert _is_quota_exhausted(body)


def test_a_plain_rate_limit_is_not_quota_exhaustion():
    """A per-minute limit clears on its own and is worth waiting out. Treating
    it as terminal would abort runs that would have finished."""
    assert not _is_quota_exhausted("Too many requests, please retry shortly")
