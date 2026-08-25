"""One dispute, all the way through.

Every stage existed and was tested before this module did; none of them had
ever been run as a single thing outside a test. That is a real gap rather than
a cosmetic one - "the parts work" and "the system works" are different claims,
and only one of them is demonstrable to someone who did not write it.

The sequence, and which half of the system owns each step:

    1 ingest      code    the dispute record
    2 triage      code    deadline tier, cited requirements, evidence gaps
    3 CE 3.0      code    prior-relationship qualification
    4 decide      code    win estimate, break-even, verdict
    5 draft       model   sentences proposed          (template by default)
    6 gate        code    claims verified or removed
    7 file        code    upload, attach, contest
    8 ledger      code    hash chain verified

Stages 5 is the only one a model touches, and stage 6 checks it. Everything
else is ordinary Python, which is why this can run with no API key at all.

Filing goes to the bundled mock in-process unless a base URL is given.
Disputes cannot be raised on demand in Razorpay test mode - they originate with
an issuing bank - so a demo that required a real one would not be a demo.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vakil.config import Settings
from vakil.decide.pipeline import Assessment, assess
from vakil.draft.facts import FactIndex, build_fact_index
from vakil.draft.gate import GateResult
from vakil.draft.letter import Drafter, TemplateDrafter, compose
from vakil.file.client import RazorpayClient
from vakil.file.filing import FilingRefused, FilingResult, file_representment
from vakil.ingest.corpus import Case, load_case
from vakil.ledger.chain import Ledger
from vakil.models import EvidenceBundle, Verdict
from vakil.rules.ce3 import qualifies_ce3


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    case: Case
    assessment: Assessment
    index: FactIndex
    letter: GateResult | None
    filing: FilingResult | None
    #: Why the run stopped short, if it did. A FOLD is not a failure - it is the
    #: system working - so this is prose rather than an error.
    stopped: str = ""

    @property
    def verdict(self) -> Verdict:
        return self.assessment.decision.verdict


def in_process_client(settings: Settings) -> RazorpayClient:
    """A client wired straight into the bundled mock, no server required.

    A demo that needs `docker compose up` first is a demo most people never
    see. This runs the same client against the same mock app in the same
    process, so the request shapes are identical to the live path.
    """
    from fastapi.testclient import TestClient

    from vakil.file.mock_razorpay import app

    http = TestClient(app)
    http.auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
    return RazorpayClient(settings, http)  # satisfies HttpClient, see client.py


def seed_mock_dispute(client: RazorpayClient, case: Case) -> None:
    """Plant the dispute so there is something to file against.

    Only meaningful for the mock. Against the real API this is a no-op, because
    a real dispute already exists - that is why there is a case at all.
    """
    client._http().put(  # noqa: SLF001 - the mock's test-only fixture endpoint
        f"/v1/_fixtures/disputes/{case.dispute.id}",
        json={
            "status": "open",
            "amount": case.dispute.amount,
            "currency": case.dispute.currency,
        },
    )


def run_case(
    path: str | Path,
    settings: Settings,
    now: datetime,
    *,
    ledger: Ledger,
    drafter: Drafter | None = None,
    client: RazorpayClient | None = None,
    drop: str | None = None,
    should_file: bool = True,
) -> RunResult:
    """Take one dispute from record to filing.

    Stops early and says why when the engine folds, escalates, or a filing gate
    refuses. None of those are errors - most disputes should end that way, and
    a runner that treated them as failures would be arguing with its own
    decision engine.
    """
    case = load_case(path)

    if drop:
        # Withdraw one evidence slot before anything reads it. This is the
        # provenance demo: the sentences that depended on it should vanish.
        bundle: EvidenceBundle = case.bundle.model_copy(update={drop: None})
        case = Case(
            case_id=case.case_id,
            dispute=case.dispute,
            bundle=bundle,
            current=case.current,
            should_win=case.should_win,
            label_basis=case.label_basis,
        )

    assessment = assess(case, settings, now)
    ce3 = qualifies_ce3(case.dispute, case.bundle, case.current)
    index = build_fact_index(case.dispute, case.bundle, ce3)

    ledger.append(
        dispute_id=case.dispute.id,
        stage="decide",
        payload=assessment.to_ledger_payload(),
        at=now,
    )

    if assessment.decision.verdict is not Verdict.FIGHT:
        return RunResult(
            case=case,
            assessment=assessment,
            index=index,
            letter=None,
            filing=None,
            stopped=(
                f"verdict is {assessment.decision.verdict} - no letter drafted and "
                "nothing filed, which is the point"
            ),
        )

    letter = compose(drafter or TemplateDrafter(), case.dispute, index, assessment.requirements)
    ledger.append(
        dispute_id=case.dispute.id,
        stage="draft",
        payload={
            "verified": len(letter.verified),
            "stripped": len(letter.stripped),
            "strip_rate": round(letter.strip_rate, 4),
            "removed": [{"text": c.text, "why": c.note} for c in letter.stripped],
        },
        at=now,
    )

    if not should_file:
        return RunResult(
            case=case,
            assessment=assessment,
            index=index,
            letter=letter,
            filing=None,
            stopped="filing skipped (--no-file)",
        )

    active = client or in_process_client(settings)
    seed_mock_dispute(active, case)

    try:
        filing = file_representment(assessment, letter, active, ledger, now)
    except FilingRefused as refusal:
        return RunResult(
            case=case,
            assessment=assessment,
            index=index,
            letter=letter,
            filing=None,
            stopped=f"filing refused: {refusal}",
        )

    return RunResult(
        case=case, assessment=assessment, index=index, letter=letter, filing=filing
    )
