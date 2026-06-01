"""Command-line entry point for the Unified Solution Migration Analyzer."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .access_manifest import render_markdown as render_access_markdown
from .config import AppConfig, load_config
from .doctor import render_report, run_checks
from .platform import default_runs_dir
from .effort import default_card, dump_card, load_card
from .effort.rate_card import RateCard
from .sources import SourceDescriptor, SourceType
from .modules.cost.analyzer import CostAnalyzer
from .modules.cost.reporting import write_reports as write_cost_reports
from .modules.dedicated_pools.analyzer import DedicatedPoolsAnalyzer
from .modules.fabric_mapping.analyzer import FabricMappingAnalyzer
from .modules.fabric_mapping.reporting import write_reports as write_fabric_reports
from .modules.fabric_validation.analyzer import FabricValidationAnalyzer
from .modules.fabric_validation.reporting import write_reports as write_fabric_validation_reports
from .modules.governance.analyzer import GovernanceAnalyzer
from .modules.governance.reporting import write_reports as write_governance_reports
from .modules.monitoring.analyzer import MonitoringAnalyzer
from .modules.monitoring.reporting import write_reports as write_monitoring_reports
from .modules.pipelines.analyzer import PipelinesAnalyzer
from .modules.pipelines.reporting import write_reports as write_pipelines_reports
from .modules.security.analyzer import SecurityAnalyzer
from .modules.security.reporting import write_reports as write_security_reports
from .modules.serverless_pools.analyzer import ServerlessPoolsAnalyzer
from .modules.serverless_pools.reporting import write_reports as write_serverless_reports
from .modules.spark_pools.analyzer import SparkPoolsAnalyzer
from .modules.spark_pools.reporting import write_reports as write_spark_reports
from .modules.storage.analyzer import StorageAnalyzer
from .modules.storage.reporting import write_reports as write_storage_reports
from .reporting import write_reports as write_dedicated_reports
from .reporting.index_report import write_index
from .reporting.run_manifest import (
    build_manifest,
    diff_manifests,
    load_manifest,
    save_manifest,
    write_delta_html,
    write_delta_json,
    write_delta_report,
)

console = Console()
log = logging.getLogger("sma")

_ALL_FORMATS = ("json", "csv", "markdown", "html")

# Deterministic exit codes for CI consumers.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_PARTIAL_FAILURES = 2
EXIT_DOCTOR_FAILED = 3

_SKIPPABLE = (
    "dedicated_pools", "serverless_pools", "spark_pools", "pipelines",
    "monitoring", "storage", "fabric_mapping",
    "governance", "security", "cost", "fabric_validation",
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _configure_logging(verbose: bool, log_format: str) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if log_format == "json":
        handler: logging.Handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(_JsonFormatter())
    else:
        handler = RichHandler(console=console, rich_tracebacks=True, show_path=False)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)


@click.group()
@click.version_option(__version__, prog_name="sma")
@click.option("-v", "--verbose", is_flag=True, help="Verbose (DEBUG) logging.")
@click.option("--env-file", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Optional path to a .env file (defaults to ./.env).")
@click.option("--log-format", type=click.Choice(["rich", "json"]), default="rich",
              help="Logging output style. 'json' emits JSONL on stderr (good for CI).")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, env_file: Path | None, log_format: str) -> None:
    """Unified Solution Migration Analyzer."""
    _configure_logging(verbose, log_format)
    ctx.ensure_object(dict)
    # Subcommands that do not need a populated .env. ``doctor`` *checks* the
    # config; ``export-schema`` is pure model introspection; ``serve`` (without
    # ``--with-api``) is a static-file webserver wrapper. ``invoked_subcommand``
    # is Click's own cleaned-up view of the dispatch target, so the detection
    # works regardless of whether the user supplied ``-v`` / ``--env-file``
    # before the subcommand name.
    _NO_CONFIG_SUBCOMMANDS = {"doctor", "export-schema", "serve", "access-report", "effort-card"}
    if ctx.invoked_subcommand in _NO_CONFIG_SUBCOMMANDS or ctx.invoked_subcommand is None:
        ctx.obj["config"] = None
        return
    # Asking for help on any subcommand should not require a .env either.
    if any(a in ("--help", "-h") for a in sys.argv[1:]):
        ctx.obj["config"] = None
        return
    try:
        ctx.obj["config"] = load_config(env_file)
    except Exception as exc:  # noqa: BLE001 - top-level CLI safety
        log.error("Configuration error: %s", exc)
        sys.exit(EXIT_CONFIG_ERROR)


@cli.command("doctor")
@click.option("--offline", is_flag=True, default=False,
              help="Skip live Azure calls (token + workspace probe).")
@click.pass_context
def doctor(ctx: click.Context, offline: bool) -> None:
    """Run a self-test: verify Python, packages, ODBC, .env, and (optionally) Azure auth."""
    report = run_checks(offline=offline)
    render_report(report, console)
    if not report.ok:
        sys.exit(EXIT_DOCTOR_FAILED)


@cli.command("access-report")
@click.option("--out", "-o", "out_path", type=click.Path(dir_okay=False, path_type=Path),
              default=None,
              help="Write the Markdown report to this path instead of stdout.")
@click.pass_context
def access_report(ctx: click.Context, out_path: Path | None) -> None:
    """Emit a Markdown report of every Azure / Synapse / SQL surface the analyzer touches.

    Suitable for InfoSec / change-advisory review. Needs no Azure access and reads no
    workspace state — the manifest is a static, version-stamped description of the
    analyzer's own RBAC + data-plane footprint.
    """
    body = render_access_markdown(__version__)
    if out_path is None:
        click.echo(body)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    _print_paths([out_path])


@cli.command("effort-card")
@click.option("--out", "-o", "out_path",
              type=click.Path(dir_okay=False, path_type=Path),
              default=Path("effort-card.json"), show_default=True,
              help="Write the shipped default rate card to this path. "
                   "Edit the resulting JSON to tune effort estimates, then "
                   "pass it back with --effort-card or via SMA_EFFORT_CARD.")
@click.pass_context
def effort_card(ctx: click.Context, out_path: Path) -> None:
    """Export the shipped default effort-card JSON so customers can tune it.

    The card governs the configurable hours-per-unit coefficients used by
    the runbook effort estimator. The shipped defaults are conservative
    middle-of-the-road numbers; customers should adjust ``team_velocity``
    and the per-rule unit hours to match their delivery actuals.
    """
    dump_card(default_card(), out_path)
    _print_paths([out_path])


def _apply_scope_flags(cfg: AppConfig, specs: tuple[str, ...]) -> AppConfig:
    """Parse repeatable ``--scope`` flags and return a new
    :class:`AppConfig` with the resulting :class:`SourceDescriptor`
    tuple installed onto ``cfg.scopes``.

    Format: ``"<source_type>:<display_name>[@<subscription_id>/<resource_group>]"``.
    Examples::

        --scope synapse_workspace:ws-eu
        --scope adf:adf-prod@11111111-1111-1111-1111-111111111111/rg-data

    Falls back to the existing ``cfg.azure.subscription_id`` when the
    optional ``@SUB/RG`` segment is omitted.
    """
    import dataclasses

    scopes: list[SourceDescriptor] = []
    for spec in specs:
        if ":" not in spec:
            raise click.UsageError(
                f"--scope value {spec!r} must use '<source_type>:<display_name>' format",
            )
        type_str, _, rest = spec.partition(":")
        type_token = type_str.strip().lower()

        # Phase 4.7: ``databricks-aws:<host>`` shortcut maps to
        # ``SourceType.DATABRICKS`` with ``extras['platform']='aws'``.
        # Any optional ``@SUB/RG`` segment is ignored (AWS workspaces
        # have no Azure subscription / resource group). See ADR-0005.
        if type_token == "databricks-aws":
            from .sources.databricks import aws_host_to_descriptor
            host = rest.partition("@")[0].strip()
            if not host:
                raise click.UsageError(
                    f"--scope value {spec!r} missing host after 'databricks-aws:'",
                )
            scopes.append(aws_host_to_descriptor(host))
            continue

        # Phase 7 Slice 7-B: ``snowflake:<account>[@<platform>]`` shortcut.
        # Snowflake accounts have no ARM concept; the optional ``@platform``
        # suffix pins ``extras['platform']`` to aws/azure/gcp. When omitted,
        # the provider sniffs ``CURRENT_REGION()`` at discover/validate
        # time (or falls back to ``SMA_SNOWFLAKE_PLATFORM`` / "aws").
        if type_token == "snowflake":
            from .sources.snowflake import host_to_descriptor as _sf_host_to_descriptor
            raw = rest.strip()
            account, _, platform_segment = raw.partition("@")
            account = account.strip()
            if not account:
                raise click.UsageError(
                    f"--scope value {spec!r} missing account after 'snowflake:'",
                )
            platform = platform_segment.strip().lower() or None
            try:
                scopes.append(_sf_host_to_descriptor(account, platform=platform))
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc
            continue

        try:
            source_type = SourceType(type_token)
        except ValueError as exc:
            allowed = ", ".join(t.value for t in SourceType) + ", databricks-aws"
            raise click.UsageError(
                f"--scope source type {type_str!r} unknown (allowed: {allowed})",
            ) from exc
        if "@" in rest:
            display, _, coords = rest.partition("@")
            sub_id, _, rg = coords.partition("/")
            sub_id = sub_id.strip() or cfg.azure.subscription_id
            rg = rg.strip() or cfg.azure.resource_group
        else:
            display = rest
            sub_id = cfg.azure.subscription_id
            rg = cfg.azure.resource_group
        display = display.strip()
        if not display:
            raise click.UsageError(
                f"--scope value {spec!r} missing display_name after ':'",
            )
        if source_type == SourceType.BIGQUERY:
            # GCP has no ARM concept; the descriptor.id IS the GCP
            # project id and subscription_id / resource_group are unset.
            # Any @SUB/RG segment provided is ignored with a warning to
            # keep the CLI surface uniform.
            scopes.append(SourceDescriptor(
                type=source_type,
                id=display,
                display_name=display,
            ))
            continue
        provider_segment = {
            SourceType.SYNAPSE_WORKSPACE: "Microsoft.Synapse/workspaces",
            SourceType.ADF: "Microsoft.DataFactory/factories",
            SourceType.DATABRICKS: "Microsoft.Databricks/workspaces",
            SourceType.SAP_BW: "ext.SAP/BW",
        }[source_type]
        arm_id = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}"
            f"/providers/{provider_segment}/{display}"
        )
        scopes.append(SourceDescriptor(
            type=source_type,
            id=arm_id,
            display_name=display,
            subscription_id=sub_id,
            resource_group=rg,
        ))
    return dataclasses.replace(cfg, scopes=tuple(scopes))


def _resolve_effort_card(card_path: Path | None) -> tuple[RateCard, str]:
    """Resolve the rate card from a CLI flag / env var / repo default.

    Returns ``(card, source)`` where ``source`` is the absolute path string
    that produced the override, or the literal ``"default"`` when the
    shipped defaults were used.
    """
    if card_path is not None:
        card = load_card(card_path)
        return card, str(card_path.resolve())
    env = os.environ.get("SMA_EFFORT_CARD")
    if env:
        card = load_card(env)
        return card, str(Path(env).resolve())
    discovered = Path("effort-card.json")
    if discovered.is_file():
        card = load_card(discovered)
        return card, str(discovered.resolve())
    return default_card(), "default"


@cli.command("analyze-dedicated-pools")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS,
              help="Report formats to emit.")
@click.pass_context
def analyze_dedicated_pools(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """Inventory and analyze dedicated SQL pools in the configured Synapse workspace."""
    cfg = ctx.obj["config"]
    result = DedicatedPoolsAnalyzer(cfg).run()
    paths = write_dedicated_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)


@cli.command("analyze-serverless-pools")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.pass_context
def analyze_serverless_pools(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """Inventory the workspace built-in serverless SQL endpoint."""
    cfg = ctx.obj["config"]
    result = ServerlessPoolsAnalyzer(cfg).run()
    paths = write_serverless_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)


@cli.command("analyze-spark-pools")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.pass_context
def analyze_spark_pools(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """Inventory Apache Spark pools in the workspace."""
    cfg = ctx.obj["config"]
    result = SparkPoolsAnalyzer(cfg).run()
    paths = write_spark_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)


@cli.command("analyze-pipelines")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.option("--since", default=None, metavar="<N>d",
              help="Run-history window, e.g. '7d' or '90d'. Overrides SMA_PIPELINES_RUN_DAYS.")
@click.option("--no-run-history", is_flag=True, default=False,
              help="Skip the pipeline run-history fetch (counts / success rate / data moved).")
@click.pass_context
def analyze_pipelines(
    ctx: click.Context,
    formats: tuple[str, ...],
    since: str | None,
    no_run_history: bool,
) -> None:
    """Inventory pipelines, linked services, datasets, triggers, integration runtimes."""
    cfg = ctx.obj["config"]
    if since:
        token = since.strip().lower()
        days_str = token[:-1] if token.endswith("d") else token
        if not days_str.isdigit():
            raise click.BadParameter(f"Expected '<N>d' or '<N>', got {since!r}", param_hint="--since")
        os.environ["SMA_PIPELINES_RUN_DAYS"] = days_str
    if no_run_history:
        os.environ["SMA_PIPELINES_RUN_HISTORY"] = "0"
    result = PipelinesAnalyzer(cfg).run()
    paths = write_pipelines_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)


@cli.command("analyze-monitoring")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.option("--since", default=None, metavar="<N>d",
              help="Time window, e.g. '7d' or '14d'. Overrides SMA_MONITORING_DAYS.")
@click.pass_context
def analyze_monitoring(ctx: click.Context, formats: tuple[str, ...], since: str | None) -> None:
    """Pull historical Azure Monitor metrics for dedicated SQL pools (DWU, queries, connections)."""
    cfg = ctx.obj["config"]
    if since:
        # Accept '7d' / '14d' / a bare integer.
        token = since.strip().lower()
        days_str = token[:-1] if token.endswith("d") else token
        if not days_str.isdigit():
            raise click.BadParameter(f"Expected '<N>d' or '<N>', got {since!r}", param_hint="--since")
        os.environ["SMA_MONITORING_DAYS"] = days_str
    result = MonitoringAnalyzer(cfg).run()
    paths = write_monitoring_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("map-to-fabric")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.option("--effort-card", "effort_card_path",
              type=click.Path(dir_okay=False, exists=True, path_type=Path),
              default=None,
              help="Override the effort-rate-card JSON used to estimate "
                   "per-step person-hours. Defaults to ./effort-card.json "
                   "if present, otherwise the shipped defaults. Generate "
                   "a starter file with `sma effort-card`.")
@click.pass_context
def map_to_fabric(
    ctx: click.Context,
    formats: tuple[str, ...],
    effort_card_path: Path | None,
) -> None:
    """Aggregate prior module outputs and produce Fabric Warehouse migration recommendations."""
    cfg = ctx.obj["config"]
    card, source = _resolve_effort_card(effort_card_path)
    result = FabricMappingAnalyzer(
        cfg, effort_card=card, effort_card_source=source,
    ).run()
    paths = write_fabric_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)


@cli.command("analyze-storage")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.pass_context
def analyze_storage(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """Inventory ADLS / storage accounts, sample capacity metrics, and size dedicated pools."""
    cfg = ctx.obj["config"]
    result = StorageAnalyzer(cfg).run()
    paths = write_storage_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("analyze-governance")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.pass_context
def analyze_governance(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """[mid-term] RBAC, managed private endpoints, CMK, Purview lineage + findings."""
    cfg = ctx.obj["config"]
    result = GovernanceAnalyzer(cfg).run()
    paths = write_governance_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("analyze-security")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.pass_context
def analyze_security(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """[mid-term] Firewall rules, AAD-only / TLS, AAD admins, pool TDE, credential inventory."""
    cfg = ctx.obj["config"]
    result = SecurityAnalyzer(cfg).run()
    paths = write_security_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("analyze-cost")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown", "html"], case_sensitive=False),
              default=_ALL_FORMATS, help="Report formats to emit.")
@click.option("--months", default=None, type=int, metavar="N",
              help="Number of months to pull from Cost Management. Overrides SMA_COST_MONTHS.")
@click.pass_context
def analyze_cost(ctx: click.Context, formats: tuple[str, ...], months: int | None) -> None:
    """[mid-term] Month-over-month consumption + Fabric capacity comparison + findings."""
    cfg = ctx.obj["config"]
    if months is not None:
        os.environ["SMA_COST_MONTHS"] = str(months)
    result = CostAnalyzer(cfg).run()
    paths = write_cost_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("validate-fabric")
@click.option("--formats", "-f", multiple=True,
              type=click.Choice(["json", "csv", "markdown"], case_sensitive=False),
              default=("json", "csv", "markdown"), help="Report formats to emit.")
@click.pass_context
def validate_fabric(ctx: click.Context, formats: tuple[str, ...]) -> None:
    """[mid-term v0] Compare prior dedicated_pools.json against a live Fabric Warehouse."""
    cfg = ctx.obj["config"]
    result = FabricValidationAnalyzer(cfg).run()
    paths = write_fabric_validation_reports(result, cfg.output_dir, formats=[f.lower() for f in formats])
    _print_paths(paths)
    if result.errors:
        sys.exit(EXIT_PARTIAL_FAILURES)


@cli.command("analyze-all")
@click.option("--skip", "skip", multiple=True,
              type=click.Choice(_SKIPPABLE, case_sensitive=False),
              help="Skip a module (repeatable). Example: --skip monitoring --skip spark_pools")
@click.option("--include", "include", multiple=True,
              type=click.Choice(("governance", "security", "cost", "fabric_validation"),
                                case_sensitive=False),
              help="Opt in to a mid-term scaffolding module (repeatable). "
                   "Example: --include governance --include security")
@click.option("--with-webui", "with_webui", is_flag=True, default=False,
              help="After the run, copy the prebuilt static SPA from web/dist/ into the "
                   "output directory so analysts get a one-folder deliverable. "
                   "Requires `cd web && npm install && npm run build` beforehand.")
@click.option("--webui-dist", "webui_dist", type=click.Path(file_okay=False, path_type=Path),
              default=None,
              help="Override the location of the prebuilt SPA (default: <repo>/web/dist).")
@click.option("--max-parallel", "max_parallel", type=click.IntRange(1, 16), default=None,
              help="Run independent modules concurrently (default: 4, override with "
                   "SMA_ANALYZE_PARALLELISM). Set to 1 for fully sequential execution "
                   "and stable log ordering.")
@click.option("--effort-card", "effort_card_path",
              type=click.Path(dir_okay=False, exists=True, path_type=Path),
              default=None,
              help="Override the effort-rate-card JSON used to estimate "
                   "per-step person-hours during map-to-fabric.")
@click.option("--scope", "scope_specs", multiple=True, metavar="TYPE:DISPLAY_NAME[@SUBSCRIPTION/RG]",
              help="Phase 1.5 — add a source scope to the run (repeatable). "
                   "Format: '<source_type>:<display_name>[@<subscription_id>/<resource_group>]'. "
                   "Examples: '--scope synapse_workspace:ws-eu' or "
                   "'--scope adf:adf-prod@11111111-1111-1111-1111-111111111111/rg-data'. "
                   "When omitted the run targets the single workspace pinned in .env.")
@click.pass_context
def analyze_all(
    ctx: click.Context,
    skip: tuple[str, ...],
    include: tuple[str, ...],
    with_webui: bool,
    webui_dist: Path | None,
    max_parallel: int | None,
    effort_card_path: Path | None,
    scope_specs: tuple[str, ...],
) -> None:
    """Run every analyzer (dedicated, serverless, spark, pipelines, monitoring, storage) then map-to-fabric."""
    cfg = ctx.obj["config"]
    # Phase 1.5 — apply repeatable --scope flags onto AppConfig.scopes.
    # Existing wave A/B execution still runs against ``cfg.azure`` (the
    # legacy single-scope path). Multi-scope dispatch lands in Phase 2.5
    # — for now this CLI surface just persists the user's intent into
    # the manifest so the SPA can render scope counts + source-type chips.
    if scope_specs:
        cfg = _apply_scope_flags(cfg, scope_specs)
        ctx.obj["config"] = cfg
        log.info(
            "analyze-all targeting %d scope(s): %s",
            len(cfg.scopes),
            ", ".join(f"{s.type.value}:{s.display_name}" for s in cfg.scopes),
        )
    effort_card, effort_card_source = _resolve_effort_card(effort_card_path)
    skipped = {s.lower() for s in skip}
    included = {i.lower() for i in include}
    # Mid-term modules are opt-in: skip them unless --include'd.
    for opt_in in ("governance", "security", "cost", "fabric_validation"):
        if opt_in not in included:
            skipped.add(opt_in)
    all_paths: list = []
    partial_failure = False

    if max_parallel is None:
        try:
            max_parallel = max(1, int(os.getenv("SMA_ANALYZE_PARALLELISM", "4")))
        except ValueError:
            max_parallel = 4

    # Wave A: every module that only depends on Azure (no on-disk inputs from
    # other modules). Wave B: modules that consume the JSON outputs of wave A.
    wave_a: list[tuple[str, str, "callable"]] = [
        ("[1/7] dedicated pools", "dedicated_pools",
         lambda: write_dedicated_reports(DedicatedPoolsAnalyzer(cfg).run(),
                                         cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[2/7] serverless pools", "serverless_pools",
         lambda: write_serverless_reports(ServerlessPoolsAnalyzer(cfg).run(),
                                          cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[3/7] spark pools", "spark_pools",
         lambda: write_spark_reports(SparkPoolsAnalyzer(cfg).run(),
                                     cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[4/7] pipelines", "pipelines",
         lambda: write_pipelines_reports(PipelinesAnalyzer(cfg).run(),
                                         cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[5/7] monitoring", "monitoring",
         lambda: write_monitoring_reports(MonitoringAnalyzer(cfg).run(),
                                          cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[6/7] storage", "storage",
         lambda: write_storage_reports(StorageAnalyzer(cfg).run(),
                                       cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[+] governance", "governance",
         lambda: write_governance_reports(GovernanceAnalyzer(cfg).run(),
                                          cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[+] security", "security",
         lambda: write_security_reports(SecurityAnalyzer(cfg).run(),
                                        cfg.output_dir, formats=list(_ALL_FORMATS))),
    ]
    wave_b: list[tuple[str, str, "callable"]] = [
        # cost optionally compares against fabric_mapping.json -- if mapping is
        # also enabled it should run AFTER mapping. fabric_mapping reads every
        # other module's JSON, so it MUST be in wave B too.
        ("[7/7] fabric mapping", "fabric_mapping",
         lambda: write_fabric_reports(FabricMappingAnalyzer(
             cfg, effort_card=effort_card, effort_card_source=effort_card_source,
         ).run(),
                                      cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[+] cost", "cost",
         lambda: write_cost_reports(CostAnalyzer(cfg).run(),
                                    cfg.output_dir, formats=list(_ALL_FORMATS))),
        ("[+] fabric_validation", "fabric_validation",
         lambda: write_fabric_validation_reports(FabricValidationAnalyzer(cfg).run(),
                                                 cfg.output_dir,
                                                 formats=["json", "csv", "markdown"])),
    ]

    def _run_wave(steps: list[tuple[str, str, "callable"]]) -> None:
        nonlocal partial_failure
        active = [(label, key, work) for (label, key, work) in steps if key not in skipped]
        for (label, key, _work) in steps:
            if key in skipped:
                log.info("%s skipped", label)
        if not active:
            return
        workers = max(1, min(max_parallel or 1, len(active)))
        if workers == 1:
            for label, _key, work in active:
                log.info(label)
                try:
                    all_paths.extend(work())
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s failed (continuing): %s", label, exc)
                    partial_failure = True
            return

        log.info("Running %d module(s) with concurrency=%d", len(active), workers)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _wrap(label: str, work):
            log.info("%s started", label)
            return label, work()

        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="sma-mod") as ex:
            futures = {ex.submit(_wrap, label, work): label
                       for (label, _key, work) in active}
            for fut in as_completed(futures):
                label = futures[fut]
                try:
                    _label, paths = fut.result()
                    all_paths.extend(paths)
                    log.info("%s done", label)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s failed (continuing): %s", label, exc)
                    partial_failure = True

    _run_wave(wave_a)
    _run_wave(wave_b)

    # Optionally drop the prebuilt static SPA into output_dir BEFORE we (re)build
    # the index, so the index can render an "Open web UI" banner when the SPA
    # is present. Best-effort: a missing `web/dist/` is a warning, not a failure
    # (the SPA build is optional).
    if with_webui:
        try:
            copied = _copy_webui(cfg.output_dir, webui_dist)
            all_paths.append(copied)
        except Exception as exc:  # noqa: BLE001
            log.warning("--with-webui copy failed (continuing): %s", exc)
            partial_failure = True

    # Always (re)build the index so users land on a single navigable page.
    try:
        all_paths.append(write_index(cfg.output_dir))
    except Exception as exc:  # noqa: BLE001
        log.warning("index.html build failed (continuing): %s", exc)
        partial_failure = True

    # Mid-term scaffolding: persist a run manifest and emit a delta report
    # against the previous one (if present).
    try:
        prev_manifest = load_manifest(cfg.output_dir)
        manifest = build_manifest(
            cfg.output_dir,
            workspace_name=cfg.azure.workspace_name,
            subscription_id=cfg.azure.subscription_id,
            resource_group=cfg.azure.resource_group,
            sma_version=__version__,
            file_names=[
                "dedicated_pools.json", "serverless_pools.json", "spark_pools.json",
                "pipelines.json", "monitoring.json", "storage.json", "fabric_mapping.json",
                "governance.json", "security.json", "cost.json", "fabric_validation.json",
            ],
        )
        all_paths.append(save_manifest(manifest, cfg.output_dir))
        diff = diff_manifests(prev_manifest, manifest)
        all_paths.append(write_delta_report(diff, cfg.output_dir))
        all_paths.append(write_delta_html(
            diff, cfg.output_dir,
            prev_manifest=prev_manifest, curr_manifest=manifest,
        ))
        all_paths.append(write_delta_json(
            diff, cfg.output_dir,
            prev_manifest=prev_manifest, curr_manifest=manifest,
        ))
    except Exception as exc:  # noqa: BLE001
        log.warning("run manifest / delta failed (continuing): %s", exc)
        partial_failure = True

    _print_paths(all_paths)
    if partial_failure:
        sys.exit(EXIT_PARTIAL_FAILURES)


def _copy_webui(output_dir: Path, webui_dist: Path | None) -> Path:
    """Copy the prebuilt SPA bundle into ``output_dir/webui/``.

    The SPA fetches JSON via relative paths (``./fabric_mapping.json``), so
    we install it into a sibling subdirectory and emit a small redirect
    page in case the analyzer's own ``index.html`` is also present. The
    SPA itself is loaded from ``output_dir/webui/index.html``.
    """
    import shutil

    src = webui_dist or (Path(__file__).resolve().parent.parent.parent / "web" / "dist")
    if not src.is_dir():
        raise FileNotFoundError(
            f"Prebuilt SPA not found at {src}. Run `cd web && npm install && npm run build` "
            "first, or pass --webui-dist <path>."
        )
    dest = output_dir / "webui"
    if dest.exists():
        shutil.rmtree(dest)
    # `dirs_exist_ok` is unnecessary because we just removed dest, but stay safe.
    shutil.copytree(src, dest)
    # Make the JSON outputs reachable from the SPA via a relative path. The
    # SPA loader fetches `./fabric_mapping.json` etc., so we copy / hardlink
    # the JSON files alongside index.html.
    for json_name in (
        "fabric_mapping.json", "dedicated_pools.json", "run_delta.json",
        "serverless_pools.json", "spark_pools.json", "pipelines.json",
        "monitoring.json", "storage.json",
        "governance.json", "security.json", "cost.json", "fabric_validation.json",
    ):
        candidate = output_dir / json_name
        if candidate.exists():
            shutil.copy2(candidate, dest / json_name)
    return dest


def _print_paths(paths: list) -> None:
    console.rule("[bold green]Reports written")
    for p in paths:
        console.print(f" - {p}")


@cli.command("index")
@click.pass_context
def build_index(ctx: click.Context) -> None:
    """Build (or refresh) `index.html` in the output directory linking all module reports."""
    cfg = ctx.obj["config"]
    path = write_index(cfg.output_dir)
    _print_paths([path])


@cli.command("run-delta")
@click.pass_context
def run_delta(ctx: click.Context) -> None:
    """[mid-term] Compare the current run against the previous run_manifest.json.

    Emits ``run_manifest.json`` (refreshed), ``run_delta.md``,
    ``run_delta.html``, and ``run_delta.json`` in the output directory.
    """
    cfg = ctx.obj["config"]
    prev = load_manifest(cfg.output_dir)
    curr = build_manifest(
        cfg.output_dir,
        workspace_name=cfg.azure.workspace_name,
        subscription_id=cfg.azure.subscription_id,
        resource_group=cfg.azure.resource_group,
        sma_version=__version__,
        file_names=[
            "dedicated_pools.json", "serverless_pools.json", "spark_pools.json",
            "pipelines.json", "monitoring.json", "storage.json", "fabric_mapping.json",
            "governance.json", "security.json", "cost.json", "fabric_validation.json",
        ],
    )
    saved = save_manifest(curr, cfg.output_dir)
    diff = diff_manifests(prev, curr)
    paths = [
        saved,
        write_delta_report(diff, cfg.output_dir),
        write_delta_html(diff, cfg.output_dir, prev_manifest=prev, curr_manifest=curr),
        write_delta_json(diff, cfg.output_dir, prev_manifest=prev, curr_manifest=curr),
    ]
    _print_paths(paths)


# Map of <output filename basename, no extension> -> top-level Pydantic model.
# Mirrors the JSON files emitted by `analyze-all`. Used by `sma export-schema`
# (and a future `sma serve` / Option B FastAPI) to publish the schemas the
# web SPA needs to type-check its loaders against.
def _schema_registry() -> dict[str, type]:
    from .modules.cost.models import CostAnalysis
    from .modules.dedicated_pools.models import WorkspaceAnalysis
    from .modules.fabric_mapping.models import FabricMappingReport
    from .modules.fabric_validation.models import FabricValidationAnalysis
    from .modules.governance.models import GovernanceAnalysis
    from .modules.monitoring.models import MonitoringAnalysis
    from .modules.pipelines.models import PipelinesAnalysis
    from .modules.security.models import SecurityAnalysis
    from .modules.serverless_pools.models import ServerlessAnalysis
    from .modules.spark_pools.models import SparkAnalysis
    from .modules.storage.models import StorageAnalysis

    return {
        "dedicated_pools": WorkspaceAnalysis,
        "serverless_pools": ServerlessAnalysis,
        "spark_pools": SparkAnalysis,
        "pipelines": PipelinesAnalysis,
        "monitoring": MonitoringAnalysis,
        "storage": StorageAnalysis,
        "fabric_mapping": FabricMappingReport,
        "governance": GovernanceAnalysis,
        "security": SecurityAnalysis,
        "cost": CostAnalysis,
        "fabric_validation": FabricValidationAnalysis,
    }


@cli.command("export-schema")
@click.option("--out-dir", "-o", type=click.Path(file_okay=False, path_type=Path),
              default=None,
              help="Directory to write per-module JSON Schema files. "
                   "Defaults to ./schemas/. The directory is created if missing.")
@click.option("--module", "-m", "modules", multiple=True,
              help="Restrict to one or more modules (repeatable). "
                   "Defaults to all modules.")
@click.pass_context
def export_schema(ctx: click.Context, out_dir: Path | None, modules: tuple[str, ...]) -> None:
    """Emit the Pydantic JSON Schema for each module's top-level model.

    Useful for code-generating ``web/src/types.ts`` (run
    ``json-schema-to-typescript`` or ``quicktype`` over the resulting
    ``schemas/*.schema.json``) and for publishing schemas from a future
    Option B FastAPI endpoint. This command needs no Azure access.
    """
    registry = _schema_registry()
    target = out_dir or Path("schemas")
    target.mkdir(parents=True, exist_ok=True)
    selected = {m.lower() for m in modules} if modules else set(registry)
    unknown = selected - set(registry)
    if unknown:
        raise click.BadParameter(
            f"Unknown module(s): {sorted(unknown)}. Known: {sorted(registry)}",
            param_hint="--module",
        )

    written: list[Path] = []
    for name in sorted(selected):
        model_cls = registry[name]
        schema = model_cls.model_json_schema()
        # Stamp the schema with the analyzer version so consumers can detect
        # drift without parsing CHANGELOG.md.
        schema["x-sma-version"] = __version__
        schema["x-sma-module"] = name
        path = target / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
        written.append(path)
    _print_paths(written)


@cli.command("serve")
@click.option("--output-dir", "-o", "output_dir",
              type=click.Path(file_okay=False, exists=True, path_type=Path),
              default=Path("output"),
              help="Directory to serve. Defaults to ./output (the analyzer's default "
                   "output_dir). Must already contain analyzer JSON / HTML output.")
@click.option("--port", "-p", type=int, default=8000, show_default=True,
              help="TCP port to bind. Pick a non-privileged port.")
@click.option("--host", "-H", default="127.0.0.1", show_default=True,
              help="Bind address. Defaults to loopback so the report is not exposed "
                   "to the LAN by default. Pass '0.0.0.0' to expose it deliberately.")
@click.option("--no-browser", "no_browser", is_flag=True, default=False,
              help="Do not open the default browser at startup.")
@click.option("--with-api", "with_api", is_flag=True, default=False,
              help="Boot the FastAPI control plane (Configuration / Run / Runs / "
                   "Diff endpoints under /api/*) instead of the read-only static "
                   "server. Requires the [web] extras: pip install -e .[web].")
@click.option("--runs-dir", "runs_dir",
              type=click.Path(file_okay=False, path_type=Path),
              default=None,
              help="Directory used as the run repository when --with-api is set. "
                   "Defaults to $SMA_RUNS_DIR, then ./runs if it exists, then the "
                   "OS per-user data dir (e.g. ~/.local/share/usma/runs on Linux, "
                   "%LOCALAPPDATA%/USMA/runs on Windows). Each run gets its own subdirectory.")
@click.option("--env-file", "env_file",
              type=click.Path(dir_okay=False, path_type=Path),
              default=Path(".env"),
              help="Path to the .env file the Configuration page reads/writes "
                   "when --with-api is set.")
@click.option("--static-dir", "static_dir",
              type=click.Path(file_okay=False, path_type=Path),
              default=None,
              help="Directory containing the prebuilt SPA bundle (web/dist/). "
                   "When omitted with --with-api, only /api/* is mounted.")
@click.option("--i-know-this-is-not-auth", "ack_no_auth", is_flag=True, default=False,
              help="Acknowledge that --with-api has no auth and you are deliberately "
                   "binding to a non-loopback host.")
def serve(
    output_dir: Path,
    port: int,
    host: str,
    no_browser: bool,
    with_api: bool,
    runs_dir: Path | None,
    env_file: Path,
    static_dir: Path | None,
    ack_no_auth: bool,
) -> None:
    """Serve the analyzer output (and optionally the FastAPI control plane).

    Without ``--with-api`` this is a read-only static file server (a
    thin wrapper around ``python -m http.server``) suitable for the SPA
    bundled by ``--with-webui``.

    With ``--with-api`` the command boots a FastAPI app that lets analysts
    drive the analyzer from the browser: edit configuration, kick off
    runs, watch progress, browse history and diffs. The API has *no
    auth* and binds to loopback by default; refuse non-loopback unless
    the operator passes ``--i-know-this-is-not-auth``.
    """
    if with_api:
        return _serve_with_api(
            host=host, port=port, no_browser=no_browser,
            runs_dir=runs_dir, env_file=env_file, static_dir=static_dir,
            ack_no_auth=ack_no_auth,
        )

    import functools
    import http.server
    import socketserver
    import threading
    import webbrowser

    output_dir = output_dir.resolve()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output_dir))
    # ThreadingHTTPServer makes Ctrl-C responsive while a request is in flight.
    server_cls = http.server.ThreadingHTTPServer if hasattr(
        http.server, "ThreadingHTTPServer"
    ) else socketserver.TCPServer

    # Prefer the SPA landing page if present, otherwise the analyzer index.
    landing = "webui/index.html" if (output_dir / "webui" / "index.html").exists() else "index.html"
    url = f"http://{host}:{port}/{landing}"

    console.rule(f"[bold green]Serving {output_dir}")
    console.print(f"  URL: [cyan]{url}[/cyan]")
    console.print(f"  Bind: {host}:{port}  (loopback-only by default; pass --host 0.0.0.0 to expose)")
    console.print("  Press Ctrl-C to stop.")

    httpd = server_cls((host, port), handler)
    if not no_browser:
        # Open the browser slightly after the server starts listening.
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[bold]Stopping server.[/bold]")
    finally:
        httpd.server_close()


def _serve_with_api(
    *,
    host: str,
    port: int,
    no_browser: bool,
    runs_dir: Path | None,
    env_file: Path,
    static_dir: Path | None,
    ack_no_auth: bool,
) -> None:
    """Boot the FastAPI control plane via uvicorn."""
    if runs_dir is None:
        runs_dir = default_runs_dir()
        runs_dir.mkdir(parents=True, exist_ok=True)
    if host not in ("127.0.0.1", "localhost", "::1") and not ack_no_auth:
        raise click.UsageError(
            f"--host {host} is not loopback and --with-api has no authentication. "
            "Re-run with --i-know-this-is-not-auth if you really want to expose it."
        )
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - explicit error path
        raise click.UsageError(
            "--with-api requires the [web] extras. Install them with: "
            "pip install -e '.[web]'"
        ) from exc

    from .web import create_app

    # Auto-discover prebuilt SPA at <repo>/web/dist when not specified.
    if static_dir is None:
        candidate = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
        if candidate.is_dir() and (candidate / "index.html").exists():
            static_dir = candidate

    app = create_app(
        runs_dir=runs_dir.resolve(),
        env_file=env_file.resolve(),
        static_dir=static_dir.resolve() if static_dir else None,
    )

    url = f"http://{host}:{port}/"
    console.rule("[bold green]Unified Solution Migration Analyzer (control plane)")
    console.print(f"  URL:       [cyan]{url}[/cyan]")
    console.print(f"  API docs:  [cyan]{url}api/docs[/cyan]")
    console.print(f"  Runs dir:  {runs_dir.resolve()}")
    console.print(f"  Env file:  {env_file.resolve()}")
    if static_dir is None:
        console.print("  [yellow]No SPA mounted. Serving /api/* only.[/yellow]")
        console.print("  Build the SPA with `cd web && npm install && npm run build`.")
    console.print(f"  Bind:      {host}:{port}  "
                  "(loopback-only by default; --with-api has NO AUTH)")
    console.print("  Press Ctrl-C to stop.")

    if not no_browser:
        import threading
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_config=None)


@cli.command("migrate-run-attribution")
@click.option(
    "--runs-dir", "runs_dir",
    type=click.Path(file_okay=False, path_type=Path), default=None,
    help="Runs directory to migrate. Defaults to the same auto-discovery used by `sma serve`.",
)
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Report what would change without writing.",
)
def migrate_run_attribution_cmd(runs_dir: Path | None, dry_run: bool) -> None:
    """Strip Azure identity from existing run.json files for non-Azure scopes.

    Rewrites pre-fix runs whose primary scope is BigQuery, Snowflake-on-AWS,
    or Databricks-on-AWS/GCP so the Estate Overview no longer mis-attributes
    them to the Azure tenant/subscription from .env.
    """
    from .web.jobs import migrate_run_attribution

    if runs_dir is None:
        runs_dir = default_runs_dir()
    runs_dir = runs_dir.resolve()
    console.print(f"Scanning [cyan]{runs_dir}[/cyan]"
                  + (" [yellow](dry-run)[/yellow]" if dry_run else ""))
    result = migrate_run_attribution(runs_dir, dry_run=dry_run)
    console.print(f"  Updated: {len(result['updated'])}")
    console.print(f"  Skipped: {len(result['skipped'])}")
    if result["errors"]:
        console.print(f"  [red]Errors:[/red]  {len(result['errors'])}")
        for line in result["errors"]:
            console.print(f"    {line}")
    for rid in result["updated"]:
        console.print(f"  [green]✓[/green] {rid}")


def main() -> None:
    try:
        cli(obj={})
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - top-level CLI safety
        log.exception("Fatal error: %s", exc)
        sys.exit(EXIT_CONFIG_ERROR)


if __name__ == "__main__":
    main()
