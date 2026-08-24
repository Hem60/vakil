"""Score document extraction against the fixture ground truth.

Three outcomes per field, and keeping them apart is the whole point:

  correct    the transcription matches ground truth
  wrong      a value was returned and it is not the truth
  abstained  the model said it could not read this field

Most extraction benchmarks collapse the last two into "not correct". That is
wrong for this system. An abstention costs a merchant one missing sentence in
a rebuttal letter; a wrong value puts a fabricated fact in a document filed
with a bank. The headline number here is therefore the **wrong rate**, not the
correct rate, and a model that abstains more but fabricates less is the better
one for this job.

Results break down by document quality, because the fixtures span clean carrier
printouts, flatbed scans and phone photographs, and an accuracy figure that
does not say which of those it came from is close to meaningless.

Usage:
  python evals/extraction_eval.py                          # stub, no key, no spend
  python evals/extraction_eval.py --backend gemini --limit 20
  python evals/extraction_eval.py --backend claude --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vakil.config import Settings  # noqa: E402
from vakil.evidence.extract import (  # noqa: E402
    ClaudeExtractor,
    ExtractionResult,
    Extractor,
    StubExtractor,
    to_delivery_proof,
)
from vakil.evidence.gemini import GeminiExtractor  # noqa: E402

DATA = ROOT / "data"
MANIFEST = DATA / "fixtures" / "MANIFEST.json"

SCORED_FIELDS = ("tracking_id", "carrier", "delivered_at", "signed_by", "delivered_to_address")

#: Per-million-token USD, by backend. Used only to report cost per case, so
#: the free tier honestly reports zero rather than an imputed price.
PRICING = {
    "claude": (5.00, 25.00),   # claude-opus-5
    "gemini": (0.0, 0.0),      # free tier
    "stub": (0.0, 0.0),
}


def normalise(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).lower().replace(",", " ").split())


def score_field(predicted: str | None, expected: str | None, legible: bool) -> str:
    """One field, one verdict.

    A field whose ground truth is null - an unsigned delivery - is scored
    correct when the model also says null. Not fabricating a signature that was
    never there is exactly the behaviour worth rewarding.
    """
    if not legible or predicted is None:
        return "correct" if expected is None else "abstained"
    return "correct" if normalise(predicted) == normalise(expected) else "wrong"


def score_document(
    result: ExtractionResult, expected: dict[str, Any], pricing: tuple[float, float]
) -> dict[str, Any]:
    outcomes: dict[str, str] = {}
    for name in SCORED_FIELDS:
        field = getattr(result.extracted, name)
        outcomes[name] = score_field(field.value, expected.get(name), field.legible)

    proof = to_delivery_proof(result)
    return {
        "outcomes": outcomes,
        "usable_proof": proof is not None,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_paise": result.cost_paise(*pricing),
    }


def tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"correct": 0, "wrong": 0, "abstained": 0}
    per_field: dict[str, dict[str, int]] = {
        f: {"correct": 0, "wrong": 0, "abstained": 0} for f in SCORED_FIELDS
    }
    for row in rows:
        for field, outcome in row["outcomes"].items():
            counts[outcome] += 1
            per_field[field][outcome] += 1

    total = sum(counts.values()) or 1
    return {
        "fields_scored": total,
        "correct_rate": round(counts["correct"] / total, 4),
        "wrong_rate": round(counts["wrong"] / total, 4),
        "abstain_rate": round(counts["abstained"] / total, 4),
        "counts": counts,
        "per_field": per_field,
    }


def run(
    extractor: Extractor,
    entries: list[dict[str, Any]],
    limit: int = 0,
    pricing: tuple[float, float] = (0.0, 0.0),
) -> dict[str, Any]:
    if limit:
        entries = entries[:limit]

    started = time.perf_counter()
    by_quality: dict[str, list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for entry in entries:
        path = DATA / entry["document"]
        if not path.exists():
            failures.append({"document": entry["document"], "error": "missing - run make fixtures"})
            continue
        try:
            result = extractor.extract(path)
        except Exception as exc:  # noqa: BLE001 - one bad page must not void the run
            failures.append(
                {"document": entry["document"], "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        row = score_document(result, entry["expected"], pricing)
        row["case_id"] = entry["case_id"]
        row["quality"] = entry["quality"]
        by_quality.setdefault(entry["quality"], []).append(row)
        all_rows.append(row)

    elapsed = time.perf_counter() - started
    tokens_in = sum(r["input_tokens"] for r in all_rows)
    tokens_out = sum(r["output_tokens"] for r in all_rows)
    cost = sum(r["cost_paise"] for r in all_rows)

    return {
        "documents": len(all_rows),
        "failures": failures,
        "overall": tally(all_rows) if all_rows else {},
        "by_quality": {q: tally(rows) for q, rows in sorted(by_quality.items())},
        "usable_proof_rate": (
            round(sum(r["usable_proof"] for r in all_rows) / len(all_rows), 4)
            if all_rows
            else 0.0
        ),
        "cost": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_paise": cost,
            "paise_per_document": round(cost / len(all_rows)) if all_rows else 0,
        },
        "seconds": round(elapsed, 1),
    }


def render(report: dict[str, Any], model: str) -> str:
    o = report["overall"]
    if not o:
        return "# Extraction evaluation\n\nNo documents scored."

    lines = [
        "# Extraction evaluation",
        "",
        f"{report['documents']} proof-of-delivery documents | model `{model}`",
        "",
        "Three outcomes, deliberately kept apart. An **abstention** costs a missing",
        "sentence in a rebuttal letter. A **wrong** value puts a fabricated fact in a",
        "document filed with a bank. The headline number is the wrong rate.",
        "",
        "| outcome | rate | n |",
        "|---|---:|---:|",
        f"| correct | {o['correct_rate']:.1%} | {o['counts']['correct']} |",
        f"| **wrong** | **{o['wrong_rate']:.1%}** | {o['counts']['wrong']} |",
        f"| abstained | {o['abstain_rate']:.1%} | {o['counts']['abstained']} |",
        "",
        f"Usable delivery proof produced for {report['usable_proof_rate']:.0%} of documents.",
        "",
        "## By document quality",
        "",
        "| quality | correct | wrong | abstained |",
        "|---|---:|---:|---:|",
    ]
    for quality, stats in report["by_quality"].items():
        lines.append(
            f"| {quality} | {stats['correct_rate']:.1%} | **{stats['wrong_rate']:.1%}** | "
            f"{stats['abstain_rate']:.1%} |"
        )

    lines += [
        "",
        "## By field",
        "",
        "| field | correct | wrong | abstained |",
        "|---|---:|---:|---:|",
    ]
    for field, counts in o["per_field"].items():
        total = sum(counts.values()) or 1
        lines.append(
            f"| `{field}` | {counts['correct'] / total:.1%} | "
            f"**{counts['wrong'] / total:.1%}** | {counts['abstained'] / total:.1%} |"
        )

    cost = report["cost"]
    lines += [
        "",
        "## Cost",
        "",
        f"{cost['input_tokens']:,} input + {cost['output_tokens']:,} output tokens | "
        f"Rs {cost['total_paise'] / 100:,.2f} total | "
        f"Rs {cost['paise_per_document'] / 100:.2f} per document | "
        f"{report['seconds']}s",
    ]

    if report["failures"]:
        lines += ["", "## Failures", ""]
        for failure in report["failures"][:10]:
            lines.append(f"- `{failure['document']}`: {failure['error']}")

    lines += [
        "",
        "> Synthetic documents rendered by data/generator/fixtures.py. Ground truth is",
        "> read from the case JSON, not re-authored, so extraction is scored against the",
        "> same source the oracle used. See docs/DATA-CARD.md.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--backend",
        choices=("claude", "gemini", "stub"),
        default="stub",
        help="which extractor to score; stub needs no key and no spend",
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    if not MANIFEST.exists():
        raise SystemExit("no fixture manifest - run `make fixtures` first")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    settings = Settings()
    extractor: Extractor
    if args.backend == "gemini":
        extractor = GeminiExtractor(settings)
        model = settings.vakil_gemini_model
    elif args.backend == "claude":
        extractor = ClaudeExtractor(settings)
        model = settings.vakil_extract_model
    else:
        extractor = StubExtractor()
        model = "stub"

    report = run(
        extractor, manifest["entries"], limit=args.limit, pricing=PRICING[args.backend]
    )
    report["model"] = model
    report["backend"] = args.backend

    # Reports are per-backend so a Gemini run never silently overwrites a
    # Claude one - the whole point is comparing them.
    out = args.out or ROOT / "evals" / f"extraction_{args.backend}.md"
    json_out = args.json_out or ROOT / "evals" / f"extraction_{args.backend}.json"
    json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render(report, model)
    out.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
