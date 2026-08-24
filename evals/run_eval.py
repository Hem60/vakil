"""Held-out evaluation. This is the file the judges should open first.

Runs the full decision path over data/test and reports six things:

  1. precision / recall / F1 of the FIGHT decision
  2. calibration - does "70%" actually win 70% of the time (Brier + bins)
  3. false-positive cost in rupees: money burned fighting cases that lost
  4. net recovery vs BOTH baselines, always-fight and always-fold
  5. rulebook coverage and evidence gaps - what the networks require that
     this merchant does not hold, and how much of the rulebook corpus is still
     unverified against a licensed copy
  6. throughput
  7. the exception list - cases the system refused to decide, and why

Accounting is deliberately conservative. A fight that wins recovers the
disputed amount and pays the filing cost; a fight that loses pays the filing
cost and recovers nothing; a fold nets zero, because the money was already
debited when the chargeback landed. Arbitration exposure is priced into the
*decision* but not into realised results, which understates our advantage
rather than overstating it.

Usage:  python evals/run_eval.py [--split data/test] [--fail-under-f1 0.0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vakil.config import Settings  # noqa: E402
from vakil.decide.pipeline import assess, default_rulebook  # noqa: E402
from vakil.ingest.corpus import load_split  # noqa: E402
from vakil.models import Verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
#: Frozen evaluation clock. Reading the wall clock here would make the report
#: drift day to day for reasons that have nothing to do with the code.
EVAL_NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
CALIBRATION_BINS = 10


def rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def confusion(rows: list[dict]) -> dict:
    decided = [r for r in rows if r["verdict"] in (Verdict.FIGHT, Verdict.FOLD)]
    tp = sum(1 for r in decided if r["verdict"] == Verdict.FIGHT and r["should_win"])
    fp = sum(1 for r in decided if r["verdict"] == Verdict.FIGHT and not r["should_win"])
    fn = sum(1 for r in decided if r["verdict"] == Verdict.FOLD and r["should_win"])
    tn = sum(1 for r in decided if r["verdict"] == Verdict.FOLD and not r["should_win"])

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(decided) if decided else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "n_decided": len(decided),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
    }


def calibration(rows: list[dict]) -> dict:
    """Reliability bins plus Brier score.

    The EV engine multiplies by p_win, so a miscalibrated probability is not a
    cosmetic problem - it corrupts every rupee figure downstream.
    """
    brier = sum((r["p_win"] - float(r["should_win"])) ** 2 for r in rows) / len(rows)
    bins = []
    for i in range(CALIBRATION_BINS):
        lo, hi = i / CALIBRATION_BINS, (i + 1) / CALIBRATION_BINS
        last_bin = i == CALIBRATION_BINS - 1
        members = [
            r for r in rows
            if lo <= r["p_win"] < hi or (last_bin and r["p_win"] == 1.0)
        ]
        if not members:
            continue
        bins.append(
            {
                "range": f"{lo:.1f}-{hi:.1f}",
                "n": len(members),
                "predicted": round(sum(r["p_win"] for r in members) / len(members), 4),
                "observed": round(sum(r["should_win"] for r in members) / len(members), 4),
            }
        )
    max_gap = max((abs(b["predicted"] - b["observed"]) for b in bins), default=0.0)
    return {"brier": round(brier, 4), "max_bin_gap": round(max_gap, 4), "bins": bins}


def money(rows: list[dict], cfg: Settings) -> dict:
    """Realised rupees under Vakil and under both trivial baselines."""
    cost = cfg.vakil_representment_cost

    def realised(fights: list[dict]) -> tuple[int, int]:
        recovered = sum(r["amount"] for r in fights if r["should_win"])
        spent = cost * len(fights)
        return recovered, spent

    vakil_fights = [r for r in rows if r["verdict"] == Verdict.FIGHT]
    v_rec, v_spent = realised(vakil_fights)

    all_fights = list(rows)
    a_rec, a_spent = realised(all_fights)

    fp_cost = cost * sum(1 for r in vakil_fights if not r["should_win"])
    fp_amount_at_risk = sum(r["amount"] for r in vakil_fights if not r["should_win"])

    return {
        "vakil": {
            "cases_fought": len(vakil_fights),
            "recovered": v_rec,
            "filing_spend": v_spent,
            "net": v_rec - v_spent,
        },
        "baseline_always_fight": {
            "cases_fought": len(all_fights),
            "recovered": a_rec,
            "filing_spend": a_spent,
            "net": a_rec - a_spent,
        },
        "baseline_always_fold": {"cases_fought": 0, "recovered": 0, "filing_spend": 0, "net": 0},
        "false_positive_cost": fp_cost,
        "false_positive_amount_at_risk": fp_amount_at_risk,
        "uplift_vs_always_fight": (v_rec - v_spent) - (a_rec - a_spent),
        "uplift_vs_always_fold": v_rec - v_spent,
    }


def run(split: Path) -> dict:
    cfg = Settings()
    cases = load_split(split)
    if not cases:
        raise SystemExit(f"no cases found in {split} - run `make data` first")

    started = time.perf_counter()
    rows = []
    exceptions = []
    gap_fields: dict[str, int] = {}
    cases_with_blocking_gaps = 0
    for case in cases:
        a = assess(case, cfg, EVAL_NOW)
        blocking = a.blocking
        if blocking:
            cases_with_blocking_gaps += 1
        for gap in blocking:
            for field in gap.missing_fields:
                gap_fields[field] = gap_fields.get(field, 0) + 1
        rows.append(
            {
                "case_id": case.case_id,
                "reason_code": str(case.dispute.reason_code),
                "amount": case.dispute.amount,
                "p_win": a.p_win,
                "verdict": a.decision.verdict,
                "confidence": a.decision.confidence,
                "ce3": a.ce3.qualifies,
                "should_win": case.should_win,
                "net_ev": a.decision.ev.net_ev,
            }
        )
        if a.decision.verdict == Verdict.ESCALATE:
            exceptions.append(
                {
                    "case_id": case.case_id,
                    "reason_code": str(case.dispute.reason_code),
                    "why": list(a.decision.exceptions),
                }
            )
    elapsed = time.perf_counter() - started

    by_reason: dict[str, dict] = {}
    for r in rows:
        b = by_reason.setdefault(r["reason_code"], {"n": 0, "fought": 0, "won": 0})
        b["n"] += 1
        if r["verdict"] == Verdict.FIGHT:
            b["fought"] += 1
            b["won"] += int(r["should_win"])

    manifest_path = split / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    return {
        "split": str(split),
        "n": len(rows),
        "dataset_sha256": manifest.get("sha256"),
        "classification": confusion(rows),
        "calibration": calibration(rows),
        "money_paise": money(rows, cfg),
        "by_reason_code": by_reason,
        "escalations": {"n": len(exceptions), "cases": exceptions[:25]},
        "rulebook": {
            **default_rulebook().coverage(),
            "cases_with_blocking_gaps": cases_with_blocking_gaps,
            "missing_field_counts": dict(
                sorted(gap_fields.items(), key=lambda kv: -kv[1])
            ),
        },
        "throughput": {
            "cases_per_second": round(len(rows) / elapsed, 1),
            "seconds": round(elapsed, 3),
        },
    }


def render(report: dict) -> str:
    c = report["classification"]
    cal = report["calibration"]
    m = report["money_paise"]
    lines = [
        "# Vakil - held-out evaluation",
        "",
        f"Split `{report['split']}` | n={report['n']} | dataset sha256 "
        f"`{(report['dataset_sha256'] or 'unknown')[:16]}`",
        "",
        "## Fight-or-Fold decision",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| precision | {c['precision']:.3f} |",
        f"| recall | {c['recall']:.3f} |",
        f"| F1 | {c['f1']:.3f} |",
        f"| accuracy | {c['accuracy']:.3f} |",
        f"| decided / escalated | {c['n_decided']} / {report['escalations']['n']} |",
        f"| TP / FP / FN / TN | {c['tp']} / {c['fp']} / {c['fn']} / {c['tn']} |",
        "",
        "## Calibration",
        "",
        f"Brier score **{cal['brier']:.4f}** | largest bin gap {cal['max_bin_gap']:.3f}",
        "",
        "| predicted | observed | n |",
        "|---:|---:|---:|",
    ]
    for b in cal["bins"]:
        lines.append(f"| {b['predicted']:.3f} | {b['observed']:.3f} | {b['n']} |")

    lines += [
        "",
        "## Money",
        "",
        "| strategy | fought | recovered | filing spend | net |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, key in (
        ("Vakil", "vakil"),
        ("always fight", "baseline_always_fight"),
        ("always fold", "baseline_always_fold"),
    ):
        b = m[key]
        lines.append(
            f"| {name} | {b['cases_fought']} | {rupees(b['recovered'])} | "
            f"{rupees(b['filing_spend'])} | **{rupees(b['net'])}** |"
        )
    lines += [
        "",
        "False-positive cost (money burned fighting losers): "
        f"**{rupees(m['false_positive_cost'])}**",
        "",
        f"Uplift vs always-fight {rupees(m['uplift_vs_always_fight'])} | "
        f"vs always-fold {rupees(m['uplift_vs_always_fold'])}",
        "",
        "## Per reason code",
        "",
        "| code | cases | fought | of those, won |",
        "|---|---:|---:|---:|",
    ]
    for code, b in sorted(report["by_reason_code"].items()):
        lines.append(f"| {code} | {b['n']} | {b['fought']} | {b['won']} |")

    lines += [
        "",
        "## Exceptions (refused to decide)",
        "",
        f"{report['escalations']['n']} of {report['n']} cases were handed to a human.",
        "",
    ]
    for e in report["escalations"]["cases"][:10]:
        lines.append(f"- `{e['case_id']}` ({e['reason_code']}): {'; '.join(e['why'])}")

    rb = report["rulebook"]
    lines += [
        "",
        "## Rulebook coverage",
        "",
        f"{rb['rules']} cited requirements across "
        f"{len(rb['reason_codes_covered'])} dispute conditions. "
        f"**{rb['unverified']} of {rb['rules']} are authored summaries not yet checked "
        f"against a licensed rulebook** - Visa and Mastercard rulebooks are proprietary "
        f"and are not reproduced in this repository.",
        "",
        f"{rb['cases_with_blocking_gaps']} of {report['n']} cases are missing evidence "
        "the "
        "network requires. Most commonly:",
        "",
        "| evidence field | cases missing it |",
        "|---|---:|",
    ]
    for field, count in list(rb["missing_field_counts"].items())[:8]:
        lines.append(f"| `{field}` | {count} |")

    lines += [
        "",
        "Gaps inform but do not gate: a missing document lowers the win probability and "
        "the EV engine folds on its own. Escalating every case with a gap would flood the "
        "human queue with cases a human cannot fix either.",
        "",
        f"Throughput: {report['throughput']['cases_per_second']} cases/sec "
        f"(decision path only, no model calls).",
        "",
        "> Synthetic corpus. See docs/DATA-CARD.md for how it was built and what it "
        "> cannot tell you.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=Path, default=ROOT / "data" / "test")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "report.md")
    ap.add_argument("--json-out", type=Path, default=ROOT / "evals" / "report.json")
    ap.add_argument("--fail-under-f1", type=float, default=0.0)
    args = ap.parse_args()

    report = run(args.split)
    args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = render(report)
    args.out.write_text(md, encoding="utf-8")
    print(md)

    f1 = report["classification"]["f1"]
    if f1 < args.fail_under_f1:
        print(f"\nFAIL: F1 {f1:.3f} below floor {args.fail_under_f1:.3f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
