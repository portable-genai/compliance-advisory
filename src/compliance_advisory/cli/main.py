"""``compliance`` — the Typer CLI for the C1 Compliance Assistant.

This is a thin presentation layer over the domain services. It owns no business
logic: every command builds the wiring from :func:`compliance_advisory.config.build_container`
and the factory functions in :mod:`compliance_advisory.api.deps`, invokes one domain
service, and pretty-prints the cited result.

Design constraints honoured here:

* **Import-safe.** Importing this module (e.g. ``compliance_advisory.cli.main:app`` as
  the ``[project.scripts]`` entry point, or ``--help``) must never pull in FastAPI,
  uvicorn, the Google Cloud SDKs, or even the domain services. All of those are imported
  *lazily inside command bodies*, so the on-prem/test profile — which installs no Google
  Cloud SDK — can still load the CLI and run ``--help``.
* **Profile-aware.** ``COMPLIANCE_PROFILE`` selects the adapter stack. The ``onprem``
  profile binds placeholder adapters that raise ``NotImplementedError`` from every method;
  when a command trips one of those, the CLI fails clearly (exit code 2) with a message
  that names the migration target rather than dumping a traceback.
* **Citations are first-class.** Answers, checklists, test cases and regulator questions
  are printed with regulator-grade provenance in ``source_id p.N — url`` form, because a
  compliance artifact that a CRO/regulator cannot trace to a page is worthless.

The CLI uses only the documented service surface from ``SPEC.md`` §5; the concrete
service classes and the ``api.deps`` factories are provided by sibling modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime top level
    from ..config import Container
    from ..domain.models import (
        Answer,
        Citation,
        ControlChecklist,
        RegulatorQuestion,
        TestCase,
    )

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "C1 Compliance Assistant — grounded, cited Q&A and control artifacts over the "
        "MAS / HKMA / APRA / FSA regulatory knowledge base, on the Gemini Enterprise "
        "Agent Platform (region asia-southeast1)."
    ),
)

corpus_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the 7-day fetch-at-runtime regulatory corpus (Agent Search + AlloyDB ledger).",
)
app.add_typer(corpus_app, name="corpus")

horizon_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Scan the regulatory horizon: diff the corpus freshness ledger, assess "
        "applicability and materiality deterministically, route ownership and track "
        "implementation to closure."
    ),
)
app.add_typer(horizon_app, name="horizon")

# Exit codes: 2 == the active profile cannot satisfy this command (e.g. on-prem stub);
# 1 == an unexpected adapter/runtime failure. 0 == success.
_PROFILE_EXIT = 2
_RUNTIME_EXIT = 1

# Identity stamped on audit events when the CLI is driven by an operator. A real
# deployment threads the authenticated principal through here.
_CLI_ACTOR = "cli:operator"


# --------------------------------------------------------------------------- #
# Wiring helpers (all imports lazy, so this module stays import-safe)
# --------------------------------------------------------------------------- #
def _container() -> Container:
    """Build the adapter container for the active ``COMPLIANCE_PROFILE``."""
    from ..config import build_container

    return build_container()


def _deps() -> Any:
    """Import the ``api.deps`` factory module lazily.

    Kept out of module scope so ``--help`` and the entry-point import never touch
    FastAPI or any adapter SDK. Raises a clear CLI error if the factory layer is
    unavailable in this build.
    """
    try:
        from ..api import deps  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - defensive wiring guard
        _fail(
            f"Service factories (compliance_advisory.api.deps) are unavailable: {exc}",
            code=_RUNTIME_EXIT,
        )
    return deps


def _fail(message: str, *, code: int = _RUNTIME_EXIT) -> Any:
    """Print a clean error to stderr and abort with ``code`` (no traceback)."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _run(action: str, fn: Any) -> Any:
    """Execute ``fn`` and translate adapter failures into clean CLI errors.

    ``NotImplementedError`` (raised by every on-prem placeholder adapter) maps to a
    profile error that names the migration target; anything else surfaces as a runtime
    error. This is the single place the CLI turns exceptions into exit codes.
    """
    from ..config import Settings

    profile = Settings.load().profile
    try:
        return fn()
    except NotImplementedError as exc:
        detail = str(exc) or "method not implemented"
        _fail(
            f"'{action}' is not available under profile '{profile}'. "
            f"This profile uses placeholder adapters (on-prem migration target): {detail}",
            code=_PROFILE_EXIT,
        )
    except KeyError as exc:
        _fail(
            f"'{action}' has no adapter wired for profile '{profile}': {exc}",
            code=_PROFILE_EXIT,
        )
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary: no tracebacks to operators
        _fail(f"'{action}' failed: {type(exc).__name__}: {exc}", code=_RUNTIME_EXIT)


# --------------------------------------------------------------------------- #
# Pretty-printing (citations are the whole point — render them precisely)
# --------------------------------------------------------------------------- #
def _fmt_citation(c: Citation) -> str:
    """Render one citation as ``[source_id, REGULATOR vX p.N] — url``."""
    page = f" p.{c.page}" if c.page is not None else ""
    version = f" {c.version}" if c.version and c.version != "unknown" else ""
    reg = c.regulator.value if hasattr(c.regulator, "value") else str(c.regulator)
    return f"[{c.source_id}, {reg}{version}{page}] — {c.url}"


def _echo_citations(citations: tuple[Citation, ...], indent: str = "  ") -> None:
    if not citations:
        typer.secho(f"{indent}(no citations)", fg=typer.colors.YELLOW)
        return
    typer.secho(f"{indent}Citations:", bold=True)
    for c in citations:
        typer.echo(f"{indent}  - {_fmt_citation(c)}")
        if c.snippet:
            snippet = c.snippet if len(c.snippet) <= 160 else c.snippet[:157] + "..."
            typer.secho(f'{indent}    "{snippet}"', fg=typer.colors.BRIGHT_BLACK)


def _echo_review_banner(requires_review: bool) -> None:
    if requires_review:
        typer.secho(
            "  [HUMAN REVIEW REQUIRED] maker-checker gate (P-06) — do not act on this "
            "output until a second qualified reviewer signs off.",
            fg=typer.colors.YELLOW,
            bold=True,
        )


def _print_answer(answer: Answer) -> None:
    typer.secho("Answer", bold=True, fg=typer.colors.GREEN)
    typer.echo(f"  Q: {answer.question}")
    typer.echo("")
    typer.echo(f"  {answer.answer}")
    typer.echo("")
    typer.echo(f"  confidence: {answer.confidence:.2f}")
    _echo_review_banner(answer.requires_human_review)
    for caveat in answer.caveats:
        typer.secho(f"  caveat: {caveat}", fg=typer.colors.YELLOW)
    _echo_citations(answer.citations)
    if answer.web_citations:
        typer.secho("  Web grounding:", bold=True)
        for wc in answer.web_citations:
            typer.echo(f"    - {wc.title} — {wc.url}")


def _print_checklist(checklist: ControlChecklist) -> None:
    typer.secho(f"Control checklist — {checklist.use_case}", bold=True, fg=typer.colors.GREEN)
    _echo_review_banner(checklist.requires_human_review)
    if not checklist.items:
        typer.secho("  (no controls produced)", fg=typer.colors.YELLOW)
        return
    for item in checklist.items:
        sev = item.severity.value if hasattr(item.severity, "value") else str(item.severity)
        typer.secho(f"  [{item.control_id}] ({sev}) {item.control}", bold=True)
        typer.echo(f"    rationale: {item.rationale}")
        _echo_citations(item.citations, indent="    ")


def _print_testcases(cases: list[TestCase]) -> None:
    typer.secho(f"Test cases ({len(cases)})", bold=True, fg=typer.colors.GREEN)
    if not cases:
        typer.secho("  (no test cases produced)", fg=typer.colors.YELLOW)
        return
    for tc in cases:
        typer.secho(f"  [{tc.id}] {tc.title}  (verifies control {tc.control_id})", bold=True)
        for i, step in enumerate(tc.steps, start=1):
            typer.echo(f"    {i}. {step}")
        typer.echo(f"    expected: {tc.expected_result}")
        if tc.automated_check:
            typer.secho(f"    automated check: {tc.automated_check}", fg=typer.colors.CYAN)
        _echo_citations(tc.citations, indent="    ")


def _print_regulator_questions(questions: list[RegulatorQuestion]) -> None:
    typer.secho(f"Regulator questions ({len(questions)})", bold=True, fg=typer.colors.GREEN)
    if not questions:
        typer.secho("  (no questions produced)", fg=typer.colors.YELLOW)
        return
    for i, q in enumerate(questions, start=1):
        reg = q.regulator.value if hasattr(q.regulator, "value") else str(q.regulator)
        typer.secho(f"  Q{i} ({reg}): {q.question}", bold=True)
        typer.echo(f"    why asked: {q.why_asked}")
        typer.echo(f"    model answer: {q.model_answer}")
        _echo_citations(q.citations, indent="    ")


def _resolve_filters(regulator: str | None) -> dict[str, str] | None:
    """Translate a ``--regulator`` flag into a structured retrieval filter.

    Validated against the domain :class:`Regulator` taxonomy so a typo fails fast
    instead of silently returning nothing.
    """
    if regulator is None:
        return None
    from ..domain.models import Regulator

    key = regulator.strip().upper()
    valid = {r.value for r in Regulator}
    if key not in valid:
        _fail(
            f"unknown regulator '{regulator}'. Choose one of: {', '.join(sorted(valid))}.",
            code=_PROFILE_EXIT,
        )
    return {"regulator": key}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def ask(
    question: str = typer.Argument(..., help="The compliance question to answer."),
    regulator: str | None = typer.Option(
        None,
        "--regulator",
        "-r",
        help="Restrict retrieval to one regulator (MAS, HKMA, APRA, FSA, CROSS).",
    ),
) -> None:
    """Answer a compliance question, grounded and cited to the source page."""
    filters = _resolve_filters(regulator)

    def _do() -> Answer:
        svc = _deps().build_qa_service(_container())
        return svc.answer(question, actor=_CLI_ACTOR, filters=filters)

    answer = _run("ask", _do)
    _print_answer(answer)


@app.command()
def checklist(
    use_case: str = typer.Argument(..., help="Use case to build a control checklist for."),
) -> None:
    """Build a use-case-specific control checklist with cited rationale."""

    def _do() -> ControlChecklist:
        svc = _deps().build_checklist_service(_container())
        return svc.build(use_case, actor=_CLI_ACTOR)

    result = _run("checklist", _do)
    _print_checklist(result)


@app.command()
def testcases(
    use_case: str = typer.Argument(..., help="Use case to generate verification test cases for."),
) -> None:
    """Generate automated test cases that verify each control for a use case."""

    def _do() -> list[TestCase]:
        svc = _deps().build_testcase_service(_container())
        return svc.generate(use_case, actor=_CLI_ACTOR)

    result = _run("testcases", _do)
    _print_testcases(result)


@app.command(name="regulator-questions")
def regulator_questions(
    use_case: str = typer.Argument(..., help="Use case to anticipate regulator/CRO questions for."),
) -> None:
    """Generate the questions a regulator/CRO will ask, with cited model answers."""

    def _do() -> list[RegulatorQuestion]:
        svc = _deps().build_regulator_question_service(_container())
        return svc.generate(use_case, actor=_CLI_ACTOR)

    result = _run("regulator-questions", _do)
    _print_regulator_questions(result)


@horizon_app.command("scan")
def horizon_scan(
    scope: str = typer.Argument(..., help="Control scope or use case the scan is run for."),
    regulator: str | None = typer.Option(
        None,
        "--regulator",
        "-r",
        help="Restrict the scan to one regulator (MAS, HKMA, APRA, FSA, CROSS).",
    ),
    tenant: str = typer.Option(
        "local", "--tenant", help="Tenant partition the tracked journey belongs to."
    ),
) -> None:
    """Detect, assess and route every regulatory change visible in the corpus ledger."""

    def _do() -> Any:
        svc = _deps().build_horizon_scan_service(_container())
        return svc.scan(scope, actor=_CLI_ACTOR, regulator=regulator, tenant=tenant)

    scan = _run("horizon scan", _do)
    _print_horizon_scan(scan)


@horizon_app.command("track")
def horizon_track(
    tenant: str = typer.Option("local", "--tenant", help="Tenant partition whose journey to list."),
    open_only: bool = typer.Option(
        False, "--open-only", help="Show only changes that are not yet closed."
    ),
) -> None:
    """Show the implementation journey for every assessed regulatory change."""

    def _do() -> Any:
        svc = _deps().build_horizon_tracking_service(_container())
        return svc.list_items(tenant, open_only=open_only)

    items = _run("horizon track", _do)
    _print_horizon_items(items)


#: Module-level singleton: a repeatable option whose default is a mutable list must not be
#: constructed in the signature (ruff B008).
_CONTROL_OPTION = typer.Option(
    None, "--control", help="GCP control id evidencing closure (repeatable)."
)


@horizon_app.command("set-status")
def horizon_set_status(
    change_id: str = typer.Argument(..., help="The assessed change to advance."),
    status: str = typer.Argument(
        ..., help="not_started | in_progress | implemented | accepted_risk | not_applicable"
    ),
    tenant: str = typer.Option("local", "--tenant", help="Tenant that owns the change."),
    note: str = typer.Option("", "--note", help="Note recorded with the transition."),
    control: list[str] = _CONTROL_OPTION,
) -> None:
    """Advance a tracked change. Closures are routed to Hrz7 for maker-checker sign-off."""
    from ..domain.horizon import ImplementationStatus

    try:
        new_status = ImplementationStatus(status)
    except ValueError:
        allowed = ", ".join(s.value for s in ImplementationStatus)
        _fail(f"unknown status {status!r}; expected one of: {allowed}")
        return

    def _do() -> Any:
        svc = _deps().build_horizon_tracking_service(_container())
        return svc.update_status(
            change_id,
            new_status,
            _CLI_ACTOR,
            tenant,
            note=note,
            control_ids=tuple(control or ()),
        )

    item = _run("horizon set-status", _do)
    _print_horizon_items([item])


@corpus_app.command("refresh")
def corpus_refresh() -> None:
    """Re-fetch and re-ingest every expired source (7-day TTL freshness pass)."""

    def _do() -> Any:
        return _deps().build_corpus_service(_container()).refresh()

    report = _run("corpus refresh", _do)
    _print_corpus_refresh(report)


@corpus_app.command("status")
def corpus_status() -> None:
    """Show the freshness ledger: which sources are fresh, expired, or missing."""

    def _do() -> Any:
        return _deps().build_corpus_service(_container()).status()

    records = _run("corpus status", _do)
    _print_corpus_status(records)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address for the API server."),
    port: int = typer.Option(8080, help="TCP port for the API server."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev only)."),
) -> None:
    """Run the FastAPI app (A2A card, MCP, and REST endpoints) under uvicorn."""

    def _do() -> None:
        import importlib

        import uvicorn

        # Import the app module up front so a misconfigured build fails before the server
        # binds a socket. The app is a module-level object (lazy and GCP-free at import, it
        # resolves adapters per request via Depends); pass it by import string so uvicorn's
        # --reload worker can re-import it.
        api_app = importlib.import_module("compliance_advisory.api.app")
        if not hasattr(api_app, "app"):
            _fail(
                "compliance_advisory.api.app does not expose 'app'; cannot start the server.",
                code=_RUNTIME_EXIT,
            )
        typer.secho(
            f"serving compliance-advisory on http://{host}:{port} (profile={_profile_label()})",
            fg=typer.colors.GREEN,
        )
        uvicorn.run(
            "compliance_advisory.api.app:app",
            host=host,
            port=port,
            reload=reload,
        )

    _run("serve", _do)


@app.command()
def eval(  # noqa: A001 - "eval" is the documented command name in SPEC §
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path to the golden eval dataset (defaults to the gate's bundled set).",
    ),
) -> None:
    """Run the A4 promotion eval gate (groundedness / citations / faithfulness / safety).

    Delegates to the ``eval/run_eval.py`` gate script (SPEC §3, A4) in a subprocess so
    the heavy Gen AI evaluation dependencies stay out of the CLI's import graph. The
    subprocess's exit code is the gate verdict and becomes this command's exit code.
    """

    def _do() -> int:
        import subprocess
        import sys
        from pathlib import Path

        # eval/ is a repo-root directory (not a package), so locate the script relative
        # to the source tree: main.py -> cli -> compliance_advisory -> src -> <repo>.
        repo_root = Path(__file__).resolve().parents[3]
        script = repo_root / "eval" / "run_eval.py"
        if not script.exists():
            _fail(f"eval gate script not found at {script}", code=_RUNTIME_EXIT)
        cmd = [sys.executable, str(script)]
        if dataset:
            cmd += ["--dataset", dataset]
        typer.secho(f"running eval gate: {script}", fg=typer.colors.CYAN)
        completed = subprocess.run(cmd, check=False)  # noqa: S603 - trusted local script
        return completed.returncode

    code = _run("eval", _do)
    if code == 0:
        typer.secho("eval gate: PASS", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("eval gate: FAIL", fg=typer.colors.RED, bold=True)
    raise typer.Exit(int(code))


# --------------------------------------------------------------------------- #
# Corpus rendering helpers (kept tolerant of the exact report/record shape)
# --------------------------------------------------------------------------- #
def _print_horizon_scan(scan: Any) -> None:
    """Print an assessed horizon scan: the decision, the arithmetic and the citation."""
    typer.secho(f"\nHorizon scan — scope {scan.scope}", bold=True)
    if not scan.assessments:
        typer.echo("  no regulatory changes detected in the corpus ledger")
        return
    summary = ", ".join(f"{k}={v}" for k, v in scan.band_summary.items())
    typer.echo(f"  materiality bands: {summary}\n")
    for a in scan.assessments:
        typer.secho(
            f"  [{a.materiality_band.value.upper()} {a.materiality_score}] "
            f"{a.change.title} ({a.change.regulator.value} {a.change.kind.value})",
            bold=True,
        )
        typer.echo(f"    applicability : {a.applicability.value}")
        for reason in a.applicability_reasons:
            typer.echo(f"      - {reason}")
        drivers = ", ".join(f"{d.name}={d.points:+d}" for d in a.drivers)
        typer.echo(f"    drivers       : {drivers}")
        if a.owner is not None:
            typer.echo(
                f"    owner         : {a.owner.owner} "
                f"(due within {a.owner.due_within_days} days) — {a.owner.reason}"
            )
        if a.narrative:
            typer.echo(f"    rationale     : {a.narrative}")
        typer.echo(f"    change id     : {a.id}")
        _echo_citations(a.citations, indent="    ")
    _echo_review_banner(scan.requires_human_review)


def _print_horizon_items(items: Any) -> None:
    """Print the tracked implementation journey."""
    typer.secho("\nRegulatory change implementation journey", bold=True)
    if not items:
        typer.echo("  nothing tracked for this tenant")
        return
    for item in items:
        typer.echo(f"  {item.status.value:<15} [{item.materiality_band.value:<8}] {item.change_id}")
        typer.echo(
            f"      source={item.source_id} owner={item.owner or 'unassigned'} "
            f"due_within_days={item.due_within_days}"
        )
        if item.control_ids:
            typer.echo(f"      controls: {', '.join(item.control_ids)}")
        if item.note:
            typer.echo(f"      note: {item.note}")


def _print_corpus_refresh(report: Any) -> None:
    typer.secho("Corpus refresh", bold=True, fg=typer.colors.GREEN)
    if report is None:
        typer.echo("  done.")
        return
    results = getattr(report, "results", report)
    if isinstance(results, list | tuple) and results:
        for r in results:
            ok = getattr(r, "ok", True)
            sid = getattr(r, "source_id", "?")
            chunks = getattr(r, "chunks", None)
            detail = getattr(r, "detail", "")
            mark = (
                typer.style("ok", fg=typer.colors.GREEN)
                if ok
                else typer.style("FAILED", fg=typer.colors.RED)
            )
            extra = f" ({chunks} chunks)" if chunks is not None else ""
            tail = f" — {detail}" if detail else ""
            typer.echo(f"  [{mark}] {sid}{extra}{tail}")
    else:
        typer.echo(f"  {report}")


def _print_corpus_status(records: Any) -> None:
    typer.secho("Corpus freshness ledger", bold=True, fg=typer.colors.GREEN)
    items = list(records) if records is not None else []
    if not items:
        typer.secho("  (ledger is empty — run 'compliance corpus refresh')", fg=typer.colors.YELLOW)
        return
    color = {
        "fresh": typer.colors.GREEN,
        "expired": typer.colors.YELLOW,
        "missing": typer.colors.BRIGHT_BLACK,
        "failed": typer.colors.RED,
    }
    for rec in items:
        status_obj = getattr(rec, "status", "unknown")
        status = status_obj.value if hasattr(status_obj, "value") else str(status_obj)
        sid = getattr(rec, "source_id", "?")
        version = getattr(rec, "version", "")
        expires = getattr(rec, "expires_at", "")
        styled = typer.style(status.upper(), fg=color.get(status, typer.colors.WHITE), bold=True)
        typer.echo(f"  {styled:<8} {sid}  v={version}  expires={expires}")


def _profile_label() -> str:
    from ..config import Settings

    return Settings.load().profile


if __name__ == "__main__":  # pragma: no cover
    app()
