"""Rendering the end-to-end run.

Separated from the runner so the pipeline stays a pure function and this stays
a presentation concern. It is also the thing a panel actually watches, so the
ordering is deliberate: each stage says what it did, and every stage is labelled
`code` or `model` so the boundary is visible rather than asserted.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vakil.config import Settings
from vakil.decide.ev import breakeven_probability
from vakil.ledger.chain import Ledger
from vakil.models import Verdict
from vakil.runner import RunResult

VERDICT_STYLE = {
    Verdict.FIGHT: "bold green",
    Verdict.FOLD: "bold yellow",
    Verdict.ESCALATE: "bold magenta",
    Verdict.PREEMPTIVE_REFUND: "bold cyan",
}


def _r(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def _stage(console: Console, number: int, name: str, owner: str) -> None:
    colour = "magenta" if owner == "model" else "cyan"
    console.print(f"\n[{colour}]{number}. {name}[/{colour}] [dim]({owner})[/dim]")


def render_run(result: RunResult, settings: Settings, ledger: Ledger) -> None:
    console = Console()
    case, a = result.case, result.assessment
    d = a.decision

    console.print(
        Panel(
            f"[bold]{case.case_id}[/bold]  dispute {case.dispute.id}\n"
            f"{case.dispute.reason_code}  {case.dispute.reason_description}\n"
            f"{_r(case.dispute.amount)}",
            title="dispute",
            expand=False,
        )
    )

    _stage(console, 1, "INGEST", "code")
    console.print(
        f"   raised {case.dispute.created_at:%Y-%m-%d}, "
        f"respond by {case.dispute.respond_by:%Y-%m-%d}"
    )

    _stage(console, 2, "TRIAGE", "code")
    console.print(f"   deadline {a.sla.tier} - {a.sla.hours_left:.1f}h remaining")
    required = [r for r in a.requirements if r.is_filable]
    console.print(f"   {len(required)} filable requirements for {case.dispute.reason_code}")
    if a.blocking:
        for gap in a.blocking[:3]:
            console.print(
                f"   [yellow]gap[/yellow] {', '.join(gap.missing_fields)} - {gap.title}"
            )
    else:
        console.print("   [green]no blocking evidence gaps[/green]")

    _stage(console, 3, "CE 3.0", "code")
    console.print(
        f"   {'[green]QUALIFIES[/green]' if a.ce3.qualifies else 'no'} - {a.ce3.reason}"
    )

    _stage(console, 4, "DECIDE", "code")
    p_star = breakeven_probability(case.dispute.amount, settings)
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("win estimate", f"{a.p_win:.3f}")
    table.add_row("break-even", f"{p_star:.3f}")
    table.add_row("margin", f"{a.p_win - p_star:+.3f}")
    table.add_row("net EV", _r(d.ev.net_ev))
    console.print(table)
    style = VERDICT_STYLE.get(d.verdict, "bold")
    console.print(
        f"   [{style}]{d.verdict}[/{style}]  confidence {d.confidence:.2f}  "
        f"{'auto-file' if d.autofile else 'human review'}"
    )

    if result.letter is not None:
        _stage(console, 5, "DRAFT", "model")
        console.print(
            f"   {len(result.index.facts)} facts held, "
            f"{len(result.letter.claims)} sentences proposed"
        )

        _stage(console, 6, "PROVENANCE GATE", "code")
        console.print(
            f"   [green]{len(result.letter.verified)} verified[/green], "
            f"[red]{len(result.letter.stripped)} removed[/red]"
        )
        for claim in result.letter.stripped[:3]:
            console.print(f"   [red]removed[/red] {claim.text}")
            console.print(f"           [dim]{claim.note}[/dim]")
        console.print()
        console.print(Panel(result.letter.body(), title="filed letter", expand=False))

    if result.filing is not None:
        _stage(console, 7, "FILE", "code")
        console.print(f"   documents: {', '.join(result.filing.document_ids)}")
        console.print(
            f"   dispute status: open -> [bold green]{result.filing.status}[/bold green]"
        )

    _stage(console, 8, "LEDGER", "code")
    ok, message = ledger.verify()
    stages = [record["event"]["stage"] for record in ledger.records()]
    console.print(f"   recorded: {' -> '.join(stages)}")
    console.print(
        f"   [{'green' if ok else 'red'}]{'OK' if ok else 'TAMPERED'}[/] {message}"
    )

    if result.stopped:
        console.print(f"\n[yellow]stopped:[/yellow] {result.stopped}")

    console.print()
