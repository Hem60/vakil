"""Cost sensitivity sweep.

Open question D5 in docs/DECISIONS.md: at the default Rs 250 filing cost,
fighting is positive-EV for nearly every case in the corpus, so Fight-or-Fold
almost never folds and cannot beat a fight-everything baseline. Is the filing
cost wrong, or does Fight-or-Fold simply pay off somewhere other than the mean?

This sweeps net realised recovery against filing cost across the plausible
range and reports where - and whether - choosing beats fighting everything. It
also splits by dispute size, because the hypothesis worth testing is that the
value concentrates in small-ticket disputes where the filing cost is a large
fraction of what is recoverable.

The answer is whatever it is. Nothing here is tuned to produce a win.

Usage:  python evals/cost_sweep.py [--split data/test]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vakil.config import Settings  # noqa: E402
from vakil.decide.pipeline import assess  # noqa: E402
from vakil.ingest.corpus import Case, load_split  # noqa: E402
from vakil.models import Verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVAL_NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)

#: Filing cost per representment, in paise. Rs 250 is roughly the marginal cost
#: to an automated system; Rs 2,500 is closer to a fully loaded manual cost with
#: an analyst spending 30-60 minutes assembling the pack.
COST_GRID = [25_000, 50_000, 80_000, 120_000, 160_000, 200_000, 250_000]


def rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def simulate(cases: list[Case], filing_cost: int) -> dict:
    """Run every case at one filing cost and realise the outcomes.

    Accounting matches evals/run_eval.py: a fight that wins recovers the
    disputed amount and pays the filing cost; a fight that loses pays the cost
    and recovers nothing; a fold nets zero, because the money was debited when
    the chargeback landed.
    """
    cfg = Settings(vakil_representment_cost=filing_cost)

    fights: list[Case] = []
    escalated_cases: list[Case] = []
    folds = 0
    for case in cases:
        verdict = assess(case, cfg, EVAL_NOW).decision.verdict
        if verdict is Verdict.FIGHT:
            fights.append(case)
        elif verdict is Verdict.FOLD:
            folds += 1
        else:
            escalated_cases.append(case)
    escalations = len(escalated_cases)

    def net(chosen: list[Case]) -> int:
        recovered = sum(c.dispute.amount for c in chosen if c.should_win)
        return recovered - filing_cost * len(chosen)

    always_fight_net = net(cases)
    wasted = filing_cost * sum(1 for c in fights if not c.should_win)

    # Escalated cases are handed to a human, so their outcome is not Vakil's to
    # claim. Crediting them with zero silently rewards abstention: raise the
    # filing cost, escalate more, and the "net" improves because the hard cases
    # quietly leave the denominator. Report the band instead.
    #
    #   optimistic - the human folds every escalated case (nets zero)
    #   pessimistic - the human fights every escalated case
    #
    # A result that only holds at the optimistic bound is a result about
    # abstention, not about deciding well.
    optimistic = net(fights)
    pessimistic = net(fights + escalated_cases)

    return {
        "filing_cost": filing_cost,
        "fought": len(fights),
        "folded": folds,
        "escalated": escalations,
        "vakil_net": optimistic,
        "vakil_net_pessimistic": pessimistic,
        "always_fight_net": always_fight_net,
        "always_fold_net": 0,
        "uplift_vs_always_fight": optimistic - always_fight_net,
        "uplift_pessimistic": pessimistic - always_fight_net,
        "robust": (optimistic - always_fight_net) > 0 and (pessimistic - always_fight_net) > 0,
        "false_positive_cost": wasted,
    }


def by_size(cases: list[Case], filing_cost: int, bands: list[tuple[str, int, int]]) -> list[dict]:
    """Same simulation, restricted to disputes inside each amount band."""
    out = []
    for label, lo, hi in bands:
        subset = [c for c in cases if lo <= c.dispute.amount < hi]
        if not subset:
            continue
        result = simulate(subset, filing_cost)
        result["band"] = label
        result["n"] = len(subset)
        out.append(result)
    return out


def size_bands(cases: list[Case]) -> list[tuple[str, int, int]]:
    """Terciles of the corpus by dispute amount, so the bands describe this
    corpus rather than a number picked to flatter the result."""
    amounts = sorted(c.dispute.amount for c in cases)
    lo = amounts[len(amounts) // 3]
    hi = amounts[2 * len(amounts) // 3]
    return [
        ("small", 0, lo),
        ("mid", lo, hi),
        ("large", hi, 10**12),
    ]


def crossover(rows: list[dict], *, robust: bool) -> int | None:
    """Lowest filing cost at which choosing beats fighting everything.

    With `robust=True` the win must hold at both bounds, so it cannot be an
    artefact of escalated cases leaving the accounting.
    """
    key = "robust" if robust else "uplift_vs_always_fight"
    for row in rows:
        if (row[key] if robust else row[key] > 0):
            return row["filing_cost"]
    return None


def run(split: Path) -> dict:
    cases = load_split(split)
    if not cases:
        raise SystemExit(f"no cases in {split} - run `make data` first")

    rows = [simulate(cases, cost) for cost in COST_GRID]
    bands = size_bands(cases)
    amounts = [c.dispute.amount for c in cases]

    return {
        "split": str(split),
        "n": len(cases),
        "amount_median": int(statistics.median(amounts)),
        "amount_min": min(amounts),
        "amount_max": max(amounts),
        "bands": [{"band": b[0], "from": b[1], "to": b[2]} for b in bands],
        "sweep": rows,
        "crossover_filing_cost": crossover(rows, robust=False),
        "crossover_filing_cost_robust": crossover(rows, robust=True),
        "by_size_at_default": by_size(cases, 25_000, bands),
        "by_size_at_manual": by_size(cases, 200_000, bands),
    }


def render(r: dict) -> str:
    lines = [
        "# Cost sensitivity sweep",
        "",
        f"Split `{r['split']}` | n={r['n']} | dispute amounts "
        f"{rupees(r['amount_min'])}-{rupees(r['amount_max'])}, "
        f"median {rupees(r['amount_median'])}",
        "",
        "Does deciding *whether* to fight beat fighting everything, and at what",
        "filing cost? Net figures are realised rupees on the held-out split.",
        "",
        "## Net recovery vs filing cost",
        "",
        "Escalated cases go to a human, so their outcome is not Vakil's to claim.",
        "Crediting them with zero would reward abstention - raise the filing cost,",
        "escalate more, and the net improves because the hard cases leave the",
        "accounting. So both bounds are reported: **opt** assumes the human folds",
        "every escalated case, **pess** assumes the human fights them all. A win",
        "that holds only at *opt* is a result about abstention, not about deciding.",
        "",
        "| filing cost | fought | folded | esc | Vakil (opt) | Vakil (pess) | always-fight "
        "| uplift opt | uplift pess |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in r["sweep"]:
        mark = "**" if row["robust"] else ""
        lines.append(
            f"| {rupees(row['filing_cost'])} | {row['fought']} | {row['folded']} | "
            f"{row['escalated']} | {rupees(row['vakil_net'])} | "
            f"{rupees(row['vakil_net_pessimistic'])} | "
            f"{rupees(row['always_fight_net'])} | "
            f"{mark}{rupees(row['uplift_vs_always_fight'])}{mark} | "
            f"{mark}{rupees(row['uplift_pessimistic'])}{mark} |"
        )

    cross = r["crossover_filing_cost"]
    robust = r["crossover_filing_cost_robust"]
    lines += [
        "",
        (
            f"**Crossover: {rupees(cross)}** at the optimistic bound."
            if cross
            else "**No crossover in the swept range** at either bound."
        ),
        (
            f" **Robust crossover: {rupees(robust)}** - holds at both bounds, so it is "
            "not an abstention artefact."
            if robust
            else " **No robust crossover:** every apparent win disappears once escalated "
            "cases are charged to Vakil, which means the sweep is measuring abstention "
            "rather than discrimination."
        ),
        "",
        "## By dispute size",
        "",
        "The hypothesis worth testing: value concentrates where the filing cost is a",
        "large fraction of what is recoverable. Bands are terciles of this corpus.",
        "",
    ]
    for label, key in (("At Rs 250 (automated marginal cost)", "by_size_at_default"),
                       ("At Rs 2,000 (fully loaded manual cost)", "by_size_at_manual")):
        lines += [
            f"### {label}",
            "",
            "| band | n | fought | folded | esc | Vakil (opt) | always-fight "
            "| uplift opt | uplift pess |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in r[key]:
            mark = "**" if row["robust"] else ""
            lines.append(
                f"| {row['band']} | {row['n']} | {row['fought']} | {row['folded']} | "
                f"{row['escalated']} | {rupees(row['vakil_net'])} | "
                f"{rupees(row['always_fight_net'])} | "
                f"{mark}{rupees(row['uplift_vs_always_fight'])}{mark} | "
                f"{mark}{rupees(row['uplift_pessimistic'])}{mark} |"
            )
        lines.append("")

    lines += [
        "> Synthetic corpus, and the corpus has no long tail of very small or very",
        "> large disputes - see docs/DATA-CARD.md. These figures describe the regime",
        "> this corpus covers, not the whole problem.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=Path, default=ROOT / "data" / "test")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "cost_sweep.md")
    ap.add_argument("--json-out", type=Path, default=ROOT / "evals" / "cost_sweep.json")
    args = ap.parse_args()

    report = run(args.split)
    args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = render(report)
    args.out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
