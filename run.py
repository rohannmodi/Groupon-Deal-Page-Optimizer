"""
Groupon Deal Page Optimizer — main entrypoint.

Usage:
    python run.py                          # process deals.txt with defaults
    python run.py --urls my_deals.txt      # custom file
    python run.py --urls deals.txt --concurrency 3
    python run.py --urls deals.txt --force # re-run all stages even if completed
    python run.py --url https://www.groupon.com/deals/...  # single URL
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from config.settings import settings
from pipeline.ingestion import load_deals
from pipeline.orchestrator import DealResult, run_pipeline
from scraper.browser import close_browser
from storage.database import close_db, get_db


console = Console()


@click.command()
@click.option(
    "--urls",
    "urls_file",
    default="deals.txt",
    show_default=True,
    help="Path to a file with one Groupon URL per line.",
)
@click.option(
    "--url",
    "single_url",
    default=None,
    help="Process a single URL (overrides --urls).",
)
@click.option(
    "--concurrency",
    default=settings.max_concurrency,
    show_default=True,
    help="Max deals to process in parallel.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-run all stages even if previously completed.",
)
@click.option(
    "--stage",
    "stage_filter",
    default=None,
    help="Run only up to this stage (scrape|report|audit_ai|research|research_ai|proposal_ai). Default: all.",
)
def main(
    urls_file: str,
    single_url: str | None,
    concurrency: int,
    force: bool,
    stage_filter: str | None,
) -> None:
    """Groupon Deal Page Optimizer — scrape, research, and optimize deal pages."""

    # ── Load deals ────────────────────────────────────────────────────────────
    if single_url:
        deals = load_deals([single_url])
    else:
        try:
            deals = load_deals(urls_file)
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)

    if not deals:
        console.print("[yellow]No deals to process.[/yellow]")
        sys.exit(0)

    # ── Initialise DB ─────────────────────────────────────────────────────────
    get_db()

    console.print(
        Panel(
            f"[bold]Groupon Deal Optimizer[/bold]\n"
            f"Deals: [cyan]{len(deals)}[/cyan]  |  "
            f"Concurrency: [cyan]{concurrency}[/cyan]  |  "
            f"Force re-run: [cyan]{force}[/cyan]  |  "
            f"Output: [cyan]{settings.outputs_dir}[/cyan]",
            expand=False,
        )
    )

    # ── Progress tracking ─────────────────────────────────────────────────────
    results: list[DealResult] = []

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    task_id = progress.add_task("Processing deals...", total=len(deals))

    def on_progress(result: DealResult) -> None:
        results.append(result)
        icon = "✅" if result.status == "success" else ("⚠️" if result.status == "partial" else "❌")
        label = result.audit.title if result.audit and result.audit.title else result.deal_id
        progress.console.print(
            f"{icon} [{result.status}] {label[:60]}"
            + (f" — {result.error}" if result.error else "")
        )
        progress.advance(task_id)

    # ── Run ───────────────────────────────────────────────────────────────────
    t_start = time.monotonic()

    async def _run() -> None:
        """Wrap pipeline + browser teardown in one event loop."""
        try:
            await run_pipeline(
                deals,
                max_concurrency=concurrency,
                force_rerun=force,
                stage_filter=stage_filter,
                on_progress=on_progress,
            )
        finally:
            # Close browser in the SAME event loop it was created in —
            # calling asyncio.run(close_browser()) after the loop exits
            # causes "future belongs to a different loop" errors.
            await close_browser()

    with progress:
        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Progress has been saved.[/yellow]")
        finally:
            close_db()

    elapsed = time.monotonic() - t_start

    # ── Summary table ─────────────────────────────────────────────────────────
    _print_summary(results, elapsed)


def _print_summary(results: list[DealResult], elapsed: float) -> None:
    n_success = sum(1 for r in results if r.status == "success")
    n_partial = sum(1 for r in results if r.status == "partial")
    n_failed = sum(1 for r in results if r.status == "failed")

    table = Table(title="Pipeline Summary", show_header=True, header_style="bold")
    table.add_column("Deal", style="cyan", max_width=40)
    table.add_column("Status", justify="center")
    table.add_column("Stages OK", justify="center")
    table.add_column("Duration")

    for r in sorted(results, key=lambda x: x.status):
        status_style = {"success": "green", "partial": "yellow", "failed": "red"}.get(r.status, "white")
        label = (r.audit.title if r.audit and r.audit.title else r.deal_id)[:40]
        table.add_row(
            label,
            f"[{status_style}]{r.status}[/{status_style}]",
            "/".join(r.stages_completed),
            f"{r.duration_seconds:.1f}s" if r.duration_seconds else "—",
        )

    console.print(table)
    console.print(
        f"\n✅ [green]{n_success} succeeded[/green]  "
        f"⚠️ [yellow]{n_partial} partial[/yellow]  "
        f"❌ [red]{n_failed} failed[/red]  "
        f"⏱ {elapsed:.1f}s total\n"
        f"Outputs in: [cyan]{settings.outputs_dir}[/cyan]"
    )


if __name__ == "__main__":
    main()
