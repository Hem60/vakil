"""Razorpay Disputes and Documents API client.

Thin on purpose. Every judgement about *whether* to file was made before
anything reaches this module; here there is only the sequence the API requires:

    POST  /v1/documents              per file, purpose=dispute_evidence
    PATCH /v1/disputes/{id}          attach the evidence object
    POST  /v1/disputes/{id}/contest  action=submit  ->  status under_review

Two things are worth reading closely.

**Authentication is HTTP Basic** with key id and secret. The secret never
appears in a log line, an exception message, or a ledger entry - `httpx` holds
it and nothing here formats it.

**The base URL is configuration, not a constant.** Disputes cannot be raised on
demand in Razorpay test mode - they originate with an issuing bank - so the
bundled mock is what exercises this path during development. Same client, same
request shapes, different host. A client that only works against a mock is not
evidence that the real one would.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from vakil.config import Settings

#: Connect fast, read patiently. Uploading a scanned document is legitimately
#: slow; a connection that has not opened in ten seconds is not going to.
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

#: Every field Razorpay's evidence object accepts. Anything else is rejected by
#: the API, so it is rejected here first with a message that names the field.
EVIDENCE_FIELDS = frozenset(
    {
        "shipping_proof",
        "billing_proof",
        "cancellation_proof",
        "customer_communication",
        "proof_of_service",
        "explanation_letter",
        "refund_confirmation",
        "access_activity_log",
        "refund_cancellation_policy",
        "term_and_conditions",
        "others",
    }
)


class RazorpayError(RuntimeError):
    """The API refused a request. Carries the status and the API's own message."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


#: An HTTP client offering `get`, `post`, `patch` and `put`, whose responses
#: carry `.status_code`, `.json()` and `.text`.
#:
#: Typed `Any` rather than `httpx.Client`, and that is not laziness. The test
#: harness passes Starlette's `TestClient`, which subclasses **`httpx2.Client`**
#: - a different package from the `httpx` used in production. Declaring
#: `httpx.Client` was simply false, and mypy caught it the moment the runner
#: needed that substitution outside a test.
#:
#: A `Protocol` was the obvious fix and does not work here: protocol parameters
#: are contravariant, so `**kwargs: Any` promises callers may pass any keyword,
#: which no real client satisfies. Expressing it precisely would mean copying
#: httpx's full signature into this file and re-copying it on every upgrade.
#:
#: The consequence is worth keeping in view rather than hiding: tests and
#: production drive two different HTTP libraries. If their response APIs
#: diverge, tests would pass while production broke. The surface this module
#: depends on is deliberately four methods and three response attributes, which
#: is small enough that the risk stays theoretical.
HttpClient = Any


class RazorpayClient:
    def __init__(self, settings: Settings, client: HttpClient | None = None) -> None:
        self._settings = settings
        self._client = client

    def _http(self) -> HttpClient:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.razorpay_base_url,
                # Basic auth, per Razorpay's API. The secret lives here and is
                # never interpolated into a string this module produces.
                auth=(self._settings.razorpay_key_id, self._settings.razorpay_key_secret),
                timeout=TIMEOUT,
            )
        return self._client

    @staticmethod
    def _unwrap(response: Any) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("description") or response.text
            except ValueError:
                detail = response.text
            raise RazorpayError(response.status_code, str(detail)[:400])
        return dict(response.json())

    # ------------------------------------------------------------ documents

    def upload_document(self, path: Path, purpose: str = "dispute_evidence") -> str:
        """Upload one file, return its document id."""
        if not path.exists():
            raise FileNotFoundError(f"cannot upload a document that is not there: {path}")

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            response = self._http().post(
                "/v1/documents",
                files={"file": (path.name, handle, mime)},
                data={"purpose": purpose},
            )
        return str(self._unwrap(response)["id"])

    # ------------------------------------------------------------- disputes

    def fetch(self, dispute_id: str) -> dict[str, Any]:
        return self._unwrap(self._http().get(f"/v1/disputes/{dispute_id}"))

    def attach_evidence(
        self, dispute_id: str, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        unknown = set(evidence) - EVIDENCE_FIELDS - {"summary", "amount"}
        if unknown:
            raise RazorpayError(
                400, f"evidence contains fields the API does not accept: {sorted(unknown)}"
            )
        return self._unwrap(
            self._http().patch(f"/v1/disputes/{dispute_id}", json={"evidence": evidence})
        )

    def contest(self, dispute_id: str) -> dict[str, Any]:
        """Submit. This is the irreversible one - it puts the merchant's case
        in front of the issuer and cannot be recalled."""
        return self._unwrap(
            self._http().post(f"/v1/disputes/{dispute_id}/contest", json={"action": "submit"})
        )

    def accept(self, dispute_id: str) -> dict[str, Any]:
        """Concede the dispute. Also irreversible, and also a decision the
        caller must already have made - this module does not choose."""
        return self._unwrap(self._http().post(f"/v1/disputes/{dispute_id}/accept", json={}))
