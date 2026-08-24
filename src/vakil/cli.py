"""Command line interface.

    vakil assess data/test/case_0007.json    decide one case, show the working
    vakil verify                             walk the audit chain
    vakil replay disp_xxx                    reconstruct a past decision
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from vakil.config import settings
from vakil.decide.ev import breakeven_probability
from vakil.decide.pipeline import assess, default_rulebook
from vakil.ingest.corpus import load_case
from vakil.ledger.chain import Ledger
from vakil.models import ReasonCode, Verdict
from vakil.rulebook.search import BM25Retriever

app = typer.Typer(add_completion=False, help="Chargeback defence agent.")
console = Console()

DEFAULT_LEDGER = Path("ledger.jsonl")
VERDICT_STYLE = {
    Verdict.FIGHT: "bold green",
    Verdict.FOLD: "bold yellow",
    Verdict.ESCALATE: "bold magenta",
    Verdict.PREEMPTIVE_REFUND: "bold cyan",
}


def _r(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


@app.command()
def assess_case(
    path: Path = typer.Argument(..., help="Path to a corpus case JSON file"),
    ledger_path: Path = typer.Option(DEFAULT_LEDGER, "--ledger"),
    now: str = typer.Option("2026-08-24T09:00:00+00:00", "--now", help="Decision clock"),
) -> None:
    """Run the decision path over one case and record it."""
    case = load_case(path)
    at = datetime.fromisoformat(now).astimezone(UTC)
    result = assess(case, settings(), at)
    d = result.decision

    console.print()
    console.print(
        f"[bold]{case.case_id}[/bold]  {case.dispute.reason_code} "
        f"{case.dispute.reason_description}  {_r(case.dispute.amount)}"
    )
    console.print(
        f"deadline {result.sla.tier} ({result.sla.hours_left:.1f}h left)   "
        f"evidence {case.bundle.completeness():.0%} complete"
    )
    console.print(f"CE 3.0: {'QUALIFIES' if result.ce3.qualifies else 'no'} - {result.ce3.reason}")
    console.print()

    table = Table(title="Expected value", show_header=True, header_style="dim")
    table.add_column("term")
    table.add_column("value", justify="right")
    ev = d.ev
    p_star = breakeven_probability(case.dispute.amount, settings())
    table.add_row("win probability", f"{ev.win_probability:.3f}")
    table.add_row("break-even probability", f"{p_star:.3f}")
    table.add_row("margin over break-even", f"{ev.win_probability - p_star:+.3f}")
    table.add_row("dispute amount", _r(ev.dispute_amount))
    table.add_row("gross expected recovery", _r(ev.gross_expected_recovery))
    table.add_row("less filing cost", f"-{_r(ev.representment_cost)}")
    table.add_row("less arbitration exposure", f"-{_r(ev.arbitration_exposure)}")
    table.add_row("net EV", _r(ev.net_ev))
    console.print(table)

    if result.gaps:
        gaps_table = Table(title="Evidence the network wants but we lack", header_style="dim")
        gaps_table.add_column("necessity")
        gaps_table.add_column("missing")
        gaps_table.add_column("rule")
        for gap in result.gaps:
            marker = " [dim](excused by CE 3.0)[/dim]" if gap.excused_by else ""
            gaps_table.add_row(
                str(gap.necessity), ", ".join(gap.missing_fields), gap.title + marker
            )
        console.print(gaps_table)
        console.print(f"[dim]cited: {result.gaps[0].citation.render()}[/dim]")

    style = VERDICT_STYLE.get(d.verdict, "bold")
    console.print(
        f"\n[{style}]{d.verdict}[/{style}]  confidence {d.confidence:.2f}  "
        f"{'auto-file' if d.autofile else 'human review'}"
    )
    console.print(f"[dim]{d.rationale}[/dim]\n")

    Ledger(ledger_path).append(
        dispute_id=case.dispute.id, stage="decide", payload=result.to_ledger_payload(), at=at
    )


@app.command()
def verify(ledger_path: Path = typer.Option(DEFAULT_LEDGER, "--ledger")) -> None:
    """Walk the audit chain and report the first broken link."""
    ok, msg = Ledger(ledger_path).verify()
    if ok:
        console.print(f"[bold green]OK[/bold green] {msg}")
    else:
        console.print(f"[bold red]TAMPERED[/bold red] {msg}")
        raise typer.Exit(1)


@app.command()
def replay(
    dispute_id: str,
    ledger_path: Path = typer.Option(DEFAULT_LEDGER, "--ledger"),
) -> None:
    """Reconstruct every recorded step for one dispute."""
    for event in Ledger(ledger_path).replay(dispute_id):
        console.print(f"[dim]{event['at']}[/dim] [bold]{event['stage']}[/bold]")
        console.print_json(data=event["payload"])


@app.command()
def rules(
    reason_code: str = typer.Argument(None, help="e.g. 13.1; omit to list coverage"),
    query: str = typer.Option(None, "--search", help="free-text search over the rulebook"),
) -> None:
    """Show what the networks require for a dispute condition, with citations."""
    book = default_rulebook()

    if query:
        code = ReasonCode(reason_code) if reason_code else None
        for hit in BM25Retriever(book).search(query, reason_code=code):
            console.print(f"[dim]{hit.score:6.2f}[/dim]  {hit.rule.title}")
            console.print(f"         [dim]{hit.rule.citation.render()}[/dim]")
        return

    if not reason_code:
        coverage = book.coverage()
        console.print(
            f"{coverage['rules']} rules, "
            f"[bold]{coverage['verified']}[/bold] checked against public documentation, "
            f"[yellow]{coverage['unverified']}[/yellow] awaiting review against a licensed rulebook"
        )
        console.print(f"reason codes covered: {', '.join(coverage['reason_codes_covered'])}")
        return

    table = Table(title=f"Requirements for {reason_code}", header_style="dim")
    table.add_column("necessity")
    table.add_column("requirement")
    table.add_column("evidence field")
    for rule in book.requirements_for(ReasonCode(reason_code)):
        mark = "" if rule.verified else " [yellow]*[/yellow]"
        table.add_row(
            str(rule.necessity), rule.title + mark, ", ".join(rule.evidence_fields) or "-"
        )
    console.print(table)
    console.print("[yellow]*[/yellow] [dim]not yet checked against a licensed rulebook[/dim]")


if __name__ == "__main__":
    app()
