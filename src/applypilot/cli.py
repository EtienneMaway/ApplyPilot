"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness for tailor/cover stages. "
            "strict: banned words = errors, judge must pass. "
            "normal: banned words = warnings only (default, recommended for Gemini free tier). "
            "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
        ),
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _bootstrap()

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        validation_mode=validation,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def apply(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max applications to submit."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel browser workers."),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for job selection."),
    model: str = typer.Option("haiku", "--model", "-m", help="Claude model name."),
    continuous: bool = typer.Option(False, "--continuous", "-c", help="Run forever, polling for new jobs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without submitting."),
    headless: bool = typer.Option(False, "--headless", help="Run browsers in headless mode."),
    url: Optional[str] = typer.Option(None, "--url", help="Apply to a specific job URL."),
    gen: bool = typer.Option(False, "--gen", help="Generate prompt file for manual debugging instead of running."),
    mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
    mark_failed: Optional[str] = typer.Option(None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Launch auto-apply to submit job applications."""
    _bootstrap()

    from applypilot.config import check_tier, PROFILE_PATH as _profile_path
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/Claude needed) ---

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    # Check 1: Tier 3 required (Claude Code CLI + Chrome)
    check_tier(3, "auto-apply")

    # Check 2: Profile exists
    if not _profile_path.exists():
        console.print(
            "[red]Profile not found.[/red]\n"
            "Run [bold]applypilot init[/bold] to create your profile first."
        )
        raise typer.Exit(code=1)

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.apply.launcher import gen_prompt, BASE_CDP_PORT
        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
        mcp_path = _profile_path.parent / ".mcp-apply-0.json"
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print(f"\n[bold]Run manually:[/bold]")
        console.print(
            f"  claude --model {model} -p "
            f"--mcp-config {mcp_path} "
            f"--permission-mode bypassPermissions < {prompt_file}"
        )
        return

    from applypilot.apply.launcher import main as apply_main

    effective_limit = limit if limit is not None else (0 if continuous else 1)

    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Limit:    {'unlimited' if continuous else effective_limit}")
    console.print(f"  Workers:  {workers}")
    console.print(f"  Model:    {model}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=continuous,
        workers=workers,
    )

    # Refresh the board, sign-up list and kits for anything that needs a person
    try:
        from applypilot.board import generate_board
        from applypilot.manual import build_kits, write_signup_md
        build_kits()
        write_signup_md()
        console.print(f"Board updated: {generate_board()}")
    except Exception as e:  # never fail an apply run over the report
        console.print(f"[yellow]Board not updated: {e}[/yellow]")


@app.command()
def history(
    days: int = typer.Option(0, "--days", "-d", help="Only show the last N days (0 = all)."),
    csv_path: Optional[str] = typer.Option(None, "--csv", help="Also write the list to this CSV file."),
    include: str = typer.Option("applied", "--include",
                                help="Statuses to show, comma separated: applied,dry_run,duplicate,failed,manual"),
) -> None:
    """Show every job you've applied to, so you know what to expect replies from."""
    _bootstrap()
    import csv
    from datetime import datetime, timedelta, timezone
    from rich.table import Table
    from applypilot.database import get_connection

    statuses = [s.strip() for s in include.split(",") if s.strip()]
    conn = get_connection()
    status_clause = f"apply_status IN ({','.join('?' * len(statuses))})"
    if "applied" in statuses:
        status_clause = f"({status_clause} OR applied_at IS NOT NULL)"
    where = [status_clause]
    params: list = list(statuses)
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        where.append("COALESCE(applied_at, last_attempted_at) >= ?")
        params.append(cutoff)
    rows = conn.execute(
        "SELECT COALESCE(applied_at, last_attempted_at) AS at, apply_status, company, title, site, "
        "fit_score, application_url, url, tailored_resume_path, cover_letter_path, apply_error "
        f"FROM jobs WHERE {' AND '.join(where)} ORDER BY at DESC",
        params,
    ).fetchall()

    table = Table(title=f"Application history ({len(rows)})", show_lines=False)
    for col in ("Date", "Status", "Company", "Title", "Board", "Fit"):
        table.add_column(col)
    for r in rows:
        table.add_row((r["at"] or "")[:10], r["apply_status"] or "", (r["company"] or "")[:28],
                      (r["title"] or "")[:45], (r["site"] or "")[:16], str(r["fit_score"] or ""))
    console.print(table)

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(rows[0].keys() if rows else ["at"])
            for r in rows:
                w.writerow(list(r))
        console.print(f"[green]Wrote {len(rows)} rows to {csv_path}[/green]")


@app.command()
def gaps(
    min_score: int = typer.Option(4, "--min-score", help="Only count jobs scored at least this high."),
    top: int = typer.Option(25, "--top", "-n", help="Rows to print."),
    set_skill: Optional[str] = typer.Option(None, "--set", help="Skill to update, e.g. Kubernetes."),
    new_status: Optional[str] = typer.Option(None, "--status", help="todo | learning | posted | skip"),
    note: Optional[str] = typer.Option(None, "--note", help="Free text, e.g. a video link."),
) -> None:
    """Skills the jobs ask for that your resume doesn't show, ranked. Writes skill_gaps.md."""
    _bootstrap()
    from rich.table import Table
    from applypilot import gaps as gaps_mod

    if set_skill:
        key = gaps_mod.set_status(set_skill, new_status or "learning", note)
        console.print(f"[green]{key}[/green] -> {new_status or 'learning'}")

    rows = gaps_mod.compute_gaps(min_score=min_score)
    path = gaps_mod.write_markdown(rows, min_score)
    table = Table(title=f"Skill gaps (jobs with fit >= {min_score})")
    for col in ("#", "Skill", "Jobs", "Share", "Avg fit", "Status", "Note"):
        table.add_column(col)
    for i, g in enumerate([r for r in rows if r["status"] != "skip"][:top], 1):
        table.add_row(str(i), g["skill"], str(g["jobs"]), f"{g['share']:.0%}", f"{g['avg_fit']:.1f}",
                      g["status"], g["note"][:40])
    console.print(table)
    console.print(f"Full list: {path}")


@app.command()
def todo() -> None:
    """Good-fit jobs the bot stopped on that need you: logins, personal questions, Easy Apply."""
    _bootstrap()
    from rich.table import Table
    from applypilot.database import get_connection

    skip = ("not_eligible_location", "not_eligible_salary", "expired", "already_applied", "not_a_job_application")
    rows = get_connection().execute(
        "SELECT fit_score, company, site, title, apply_error, COALESCE(application_url, url) AS link FROM jobs "
        "WHERE apply_status IN ('failed', 'manual') AND applied_at IS NULL "
        f"AND COALESCE(apply_error, '') NOT IN ({','.join('?' * len(skip))}) "
        "ORDER BY fit_score DESC",
        skip,
    ).fetchall()
    table = Table(title=f"Needs you ({len(rows)})", show_lines=True)
    for col in ("Fit", "Company", "Title", "Why it stopped", "Link"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(str(r["fit_score"]), r["company"] or r["site"], r["title"],
                      (r["apply_error"] or "").replace("_", " "), r["link"])
    console.print(table)
    console.print("After applying by hand: applypilot apply --mark-applied URL")


@app.command()
def board(no_open: bool = typer.Option(False, "--no-open", help="Write the page without opening it.")) -> None:
    """Build the local job-hunt page (applied, needs you, sign up, skill gaps, content) and open it."""
    _bootstrap()
    from applypilot.board import generate_board
    from applypilot.gaps import compute_gaps, write_markdown
    from applypilot.manual import write_signup_md
    write_signup_md()
    write_markdown(compute_gaps(min_score=4), 4)
    path = generate_board(open_browser=not no_open)
    console.print(f"[green]Board:[/green] {path}")


@app.command()
def kits(rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild kits that already exist.")) -> None:
    """Build copy-paste kits (resume, cover letter, answers) for jobs you'll apply to by hand."""
    _bootstrap()
    from applypilot.manual import build_kits
    n = build_kits(rebuild=rebuild)
    console.print(f"[green]{n} kit(s) built.[/green] Open them with: applypilot board")


@app.command()
def signups(retry: bool = typer.Option(False, "--retry", help="After signing up: retry those jobs with your logged-in Chrome.")) -> None:
    """Sites that need an account before the bot can apply. Writes sign_up.md."""
    _bootstrap()
    from applypilot.manual import retry_signup_jobs, signup_sites, write_signup_md
    if retry:
        n = retry_signup_jobs()
        console.print(f"[green]{n} job(s) back in the queue.[/green] Chrome profile will be re-copied on the next apply.")
        return
    sites = signup_sites()
    path = write_signup_md(sites)
    for s in sites:
        console.print(f"  {s['domain']}: {len(s['jobs'])} job(s), best fit {s['best_fit']} -> {s['signup_url']}")
    if not sites:
        console.print("  Nothing waiting.")
    console.print(f"List: {path}")


@app.command()
def content(min_score: int = typer.Option(4, "--min-score", help="Use jobs scored at least this high.")) -> None:
    """Ideas for posts, videos and repos aimed at the companies you're applying to."""
    _bootstrap()
    from applypilot.content import generate_ideas
    n = generate_ideas(min_score=min_score)
    console.print(f"[green]{n} ideas saved.[/green] See them with: applypilot board")


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, ENV_PATH, get_chrome_path,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'applypilot init'"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex"))

    # --- Tier 2 checks ---
    import os
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_local = bool(os.environ.get("LLM_URL"))
    if has_gemini:
        model = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
        results.append(("LLM API key", ok_mark, f"Gemini ({model})"))
    elif has_openai:
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        results.append(("LLM API key", ok_mark, f"OpenAI ({model})"))
    elif has_local:
        results.append(("LLM API key", ok_mark, f"Local: {os.environ.get('LLM_URL')}"))
    else:
        results.append(("LLM API key", fail_mark,
                        "Set GEMINI_API_KEY in ~/.applypilot/.env (run 'applypilot init')"))

    # --- Tier 3 checks ---
    # Claude Code CLI
    claude_bin = shutil.which("claude")
    if claude_bin:
        results.append(("Claude Code CLI", ok_mark, claude_bin))
    else:
        results.append(("Claude Code CLI", fail_mark,
                        "Install from https://claude.ai/code (needed for auto-apply)"))

    # Chrome
    try:
        chrome_path = get_chrome_path()
        results.append(("Chrome/Chromium", ok_mark, chrome_path))
    except FileNotFoundError:
        results.append(("Chrome/Chromium", fail_mark,
                        "Install Chrome or set CHROME_PATH env var (needed for auto-apply)"))

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(("Node.js (npx)", fail_mark,
                        "Install Node.js 18+ from nodejs.org (needed for auto-apply)"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring, tailoring, cover letters (needs LLM API key)[/dim]")
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")
    elif tier == 2:
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")

    console.print()


if __name__ == "__main__":
    app()
