"""Audit ledger tests. The point of the ledger is that it fails loudly when
someone edits history, so most of these tests are about breaking it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vakil.ledger.chain import GENESIS, Ledger, LedgerError

AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def make(tmp_path: Path, stages: tuple[str, ...] = ("ingest", "triage", "decide")) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i, stage in enumerate(stages):
        ledger.append(dispute_id="disp_1", stage=stage, payload={"step": i}, at=AT)
    return ledger


def test_empty_ledger_head_is_genesis(tmp_path: Path):
    assert Ledger(tmp_path / "l.jsonl").head() == GENESIS


def test_intact_chain_verifies(tmp_path: Path):
    ok, msg = make(tmp_path).verify()
    assert ok
    assert "intact" in msg


def test_altered_payload_breaks_the_chain(tmp_path: Path):
    ledger = make(tmp_path)
    lines = ledger.path.read_text().splitlines()
    record = json.loads(lines[1])
    record["event"]["payload"]["step"] = 999
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    ledger.path.write_text("\n".join(lines) + "\n")

    ok, msg = ledger.verify()
    assert not ok
    assert "triage" in msg and "altered" in msg


def test_deleted_record_breaks_the_chain(tmp_path: Path):
    """Removing an inconvenient entry must be as visible as editing one."""
    ledger = make(tmp_path)
    lines = ledger.path.read_text().splitlines()
    del lines[1]
    ledger.path.write_text("\n".join(lines) + "\n")

    ok, msg = ledger.verify()
    assert not ok
    assert "prev-hash mismatch" in msg


def test_replay_returns_only_that_dispute_in_order(tmp_path: Path):
    ledger = make(tmp_path)
    ledger.append(dispute_id="disp_2", stage="ingest", payload={}, at=AT)

    events = ledger.replay("disp_1")
    assert [e["stage"] for e in events] == ["ingest", "triage", "decide"]


def test_replay_refuses_a_broken_chain(tmp_path: Path):
    """Replaying tampered history would launder it. Refuse instead."""
    ledger = make(tmp_path)
    lines = ledger.path.read_text().splitlines()
    del lines[0]
    ledger.path.write_text("\n".join(lines) + "\n")

    with pytest.raises(LedgerError, match="broken chain"):
        ledger.replay("disp_1")


def test_appends_are_hash_linked(tmp_path: Path):
    ledger = make(tmp_path)
    records = list(ledger.records())
    assert records[0]["prev"] == GENESIS
    for earlier, later in zip(records, records[1:], strict=False):
        assert later["prev"] == earlier["hash"]
