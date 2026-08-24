"""Hash-chained audit ledger.

Every decision the pipeline makes is appended here: what was retrieved, which
rule fired, what the model was asked, what the EV maths produced, what was
filed. Each record carries the hash of the one before it, so altering any
historical entry breaks every hash after it and `verify()` says so.

    hash_n = sha256(hash_{n-1} || canonical_json(event_n))

This is not a database. It is a receipt. When a payments panel asks "how do I
know the agent did what it says it did", this is the answer - and `replay()`
reconstructs any past case exactly, because every input was recorded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class LedgerError(RuntimeError):
    pass


def _canonical(obj: Any) -> str:
    """Stable serialisation. Sorted keys and no incidental whitespace, or the
    same event would hash differently on two machines."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(event)).encode()).hexdigest()


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- write

    def append(
        self,
        *,
        dispute_id: str,
        stage: str,
        payload: dict[str, Any],
        at: datetime,
    ) -> str:
        event = {
            "dispute_id": dispute_id,
            "stage": stage,
            "at": at.isoformat(),
            "payload": payload,
        }
        prev = self.head()
        h = _digest(prev, event)
        record = {"hash": h, "prev": prev, "event": event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(record) + "\n")
        return h

    # ----------------------------------------------------------------- read

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def head(self) -> str:
        last = GENESIS
        for rec in self.records():
            last = rec["hash"]
        return last

    # --------------------------------------------------------------- verify

    def verify(self) -> tuple[bool, str]:
        """Walk the chain. Returns (ok, message). The message names the first
        broken link, because 'something is wrong' is not an audit finding."""
        prev = GENESIS
        for i, rec in enumerate(self.records()):
            if rec.get("prev") != prev:
                return False, f"record {i} ({rec['event'].get('stage')}): prev-hash mismatch"
            expected = _digest(prev, rec["event"])
            if rec.get("hash") != expected:
                return False, (
                    f"record {i} ({rec['event'].get('stage')}) for "
                    f"{rec['event'].get('dispute_id')}: content altered after write"
                )
            prev = rec["hash"]
        return True, f"chain intact, head {prev[:12]}"

    # --------------------------------------------------------------- replay

    def replay(self, dispute_id: str) -> list[dict[str, Any]]:
        ok, msg = self.verify()
        if not ok:
            raise LedgerError(f"refusing to replay a broken chain: {msg}")
        return [r["event"] for r in self.records() if r["event"]["dispute_id"] == dispute_id]
