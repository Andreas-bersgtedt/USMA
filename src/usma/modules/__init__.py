"""Pluggable analysis modules.

This package exposes a central :data:`MODULE_REGISTRY` that maps each
analyzer module name to a callable returning ``(result, write_reports)``.
Both the CLI (``cli.analyze_all``) and the web :mod:`~.web.jobs` runner
should consume this registry instead of duplicating dispatch logic.

Each factory accepts a :class:`~..progress.ProgressReporter`. The CLI
passes :class:`~..progress.NullProgress` (no-op). The web runner passes a
:class:`~..progress.CallbackProgress` that forwards to the SSE channel.

Analyzer + reporting code is imported lazily inside each factory so that
``import usma.modules`` stays cheap (no Azure SDK
load just to enumerate module names).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..progress import NullProgress, ProgressReporter

ModuleFactory = Callable[
    [AppConfig, ProgressReporter], tuple[Any, Callable[..., list[Path]]]
]


def _dedicated(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .dedicated_pools.analyzer import DedicatedPoolsAnalyzer
    from ..reporting import write_reports

    return DedicatedPoolsAnalyzer(cfg, progress=progress).run(), write_reports


def _serverless(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .serverless_pools.analyzer import ServerlessPoolsAnalyzer
    from .serverless_pools.reporting import write_reports

    return ServerlessPoolsAnalyzer(cfg, progress=progress).run(), write_reports


def _spark(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .spark_pools.analyzer import SparkPoolsAnalyzer
    from .spark_pools.reporting import write_reports

    return SparkPoolsAnalyzer(cfg, progress=progress).run(), write_reports


def _pipelines(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .pipelines.analyzer import PipelinesAnalyzer
    from .pipelines.reporting import write_reports

    # Phase 2.5 — when the active scope is non-Synapse (today: ADF),
    # route through PipelinesAnalyzer.for_descriptor() so the analyzer
    # gets the right PipelineCollector (e.g. AdfCollector) instead of
    # the default SynapseCollector. The Synapse path is unchanged.
    primary = cfg.primary_scope()
    if primary is not None:
        from ..sources import Credentials as _Creds, SourceType as _ST

        if primary.type is _ST.ADF:
            creds = _Creds(
                tenant_id=cfg.azure.tenant_id,
                client_id=cfg.azure.client_id,
                client_secret=cfg.azure.client_secret,
            )
            analyzer = PipelinesAnalyzer.for_descriptor(
                cfg, primary, creds, progress=progress,
            )
            return analyzer.run(), write_reports

    return PipelinesAnalyzer(cfg, progress=progress).run(), write_reports


def _monitoring(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .monitoring.analyzer import MonitoringAnalyzer
    from .monitoring.reporting import write_reports

    return MonitoringAnalyzer(cfg, progress=progress).run(), write_reports


def _storage(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .storage.analyzer import StorageAnalyzer
    from .storage.reporting import write_reports

    return StorageAnalyzer(cfg, progress=progress).run(), write_reports


def _fabric_mapping(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    import os
    from pathlib import Path as _Path

    from ..effort import default_card, load_card
    from .fabric_mapping.analyzer import FabricMappingAnalyzer
    from .fabric_mapping.reporting import write_reports

    # Look for an override card next to the run output dir or in CWD.
    # SMA_EFFORT_CARD wins, then ./effort-card.json (relative to CWD),
    # then the shipped defaults.
    card = default_card()
    source = "default"
    env = os.environ.get("SMA_EFFORT_CARD")
    if env and _Path(env).is_file():
        card = load_card(env)
        source = str(_Path(env).resolve())
    elif _Path("effort-card.json").is_file():
        card = load_card("effort-card.json")
        source = str(_Path("effort-card.json").resolve())
    return (
        FabricMappingAnalyzer(
            cfg, progress=progress, effort_card=card, effort_card_source=source,
        ).run(),
        write_reports,
    )


def _governance(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .governance.analyzer import GovernanceAnalyzer
    from .governance.reporting import write_reports

    return GovernanceAnalyzer(cfg, progress=progress).run(), write_reports


def _security(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .security.analyzer import SecurityAnalyzer
    from .security.reporting import write_reports

    return SecurityAnalyzer(cfg, progress=progress).run(), write_reports


def _cost(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .cost.analyzer import CostAnalyzer
    from .cost.reporting import write_reports

    return CostAnalyzer(cfg, progress=progress).run(), write_reports


def _fabric_validation(cfg: AppConfig, progress: ProgressReporter) -> tuple[Any, Callable[..., list[Path]]]:
    from .fabric_validation.analyzer import FabricValidationAnalyzer
    from .fabric_validation.reporting import write_reports

    return FabricValidationAnalyzer(cfg, progress=progress).run(), write_reports


def _databricks_workflows(
    cfg: AppConfig, progress: ProgressReporter,
) -> tuple[Any, Callable[..., list[Path]]]:
    """Databricks workflows + clusters (Phase 4, Slice 4-B).

    Requires the primary scope to be a Databricks descriptor; raises a
    clear ``RuntimeError`` otherwise so the run planner / SPA gating
    catch any misconfiguration before this factory is called.
    """
    from .databricks_workflows.analyzer import DatabricksWorkflowsAnalyzer
    from .databricks_workflows.reporting import write_reports
    from ..sources import Credentials as _Creds, SourceType as _ST

    primary = cfg.primary_scope()
    if primary is None or primary.type is not _ST.DATABRICKS:
        raise RuntimeError(
            "databricks_workflows requires a Databricks scope as the "
            "active descriptor (got "
            f"{getattr(primary, 'type', None)!r})",
        )
    # Forward Databricks-on-AWS env vars via ``extras`` so the
    # ``DatabricksAwsProvider`` can build a workspace client from the
    # single Databricks service principal (DATABRICKS_CLIENT_ID +
    # DATABRICKS_CLIENT_SECRET). Azure platform descriptors ignore
    # these extras and use the Azure SP fields instead.
    import os as _os
    extras: dict[str, str] = {}
    for _k in (
        "DATABRICKS_HOST",
        "DATABRICKS_ACCOUNT_ID",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        _v = (_os.environ.get(_k) or "").strip()
        if _v:
            extras[_k] = _v
    creds = _Creds(
        tenant_id=cfg.azure.tenant_id,
        client_id=cfg.azure.client_id,
        client_secret=cfg.azure.client_secret,
        extras=extras,
    )
    analyzer = DatabricksWorkflowsAnalyzer.for_descriptor(
        cfg, primary, creds, progress=progress,
    )
    return analyzer.run(), write_reports


def _bigquery_workloads(
    cfg: AppConfig, progress: ProgressReporter,
) -> tuple[Any, Callable[..., list[Path]]]:
    """BigQuery datasets / tables / routines / scheduled queries / jobs (Phase 5 Slice 5-B).

    Requires the primary scope to be a BigQuery descriptor. The provider
    resolves Google ADC credentials internally — the Azure-shaped
    ``Credentials`` object is **not** used (see ``sources/bigquery/provider.py``).
    """
    from .bigquery_workloads.analyzer import BigQueryWorkloadsAnalyzer
    from .bigquery_workloads.reporting import write_reports
    from ..sources import SourceType as _ST

    primary = cfg.primary_scope()
    if primary is None or primary.type is not _ST.BIGQUERY:
        raise RuntimeError(
            "bigquery_workloads requires a BigQuery scope as the "
            "active descriptor (got "
            f"{getattr(primary, 'type', None)!r})",
        )
    analyzer = BigQueryWorkloadsAnalyzer.for_descriptor(
        cfg, primary, None, progress=progress,
    )
    return analyzer.run(), write_reports


def _snowflake_workloads(
    cfg: AppConfig, progress: ProgressReporter,
) -> tuple[Any, Callable[..., list[Path]]]:
    """Snowflake warehouses / databases / schemas / tables / routines / tasks / jobs (Phase 7 Slice 7-C).

    Requires the primary scope to be a Snowflake descriptor. The provider
    resolves OAuth credentials internally via ``Credentials.extras``
    + ``SNOWFLAKE_*`` env-var fallback (see ``sources/snowflake/provider.py``).
    """
    from .snowflake_workloads.analyzer import SnowflakeWorkloadsAnalyzer
    from .snowflake_workloads.reporting import write_reports
    from ..sources import Credentials as _Creds, SourceType as _ST

    primary = cfg.primary_scope()
    if primary is None or primary.type is not _ST.SNOWFLAKE:
        raise RuntimeError(
            "snowflake_workloads requires a Snowflake scope as the "
            "active descriptor (got "
            f"{getattr(primary, 'type', None)!r})",
        )
    creds = _Creds(
        tenant_id=cfg.azure.tenant_id,
        client_id=cfg.azure.client_id,
        client_secret=cfg.azure.client_secret,
    )
    analyzer = SnowflakeWorkloadsAnalyzer.for_descriptor(
        cfg, primary, creds, progress=progress,
    )
    return analyzer.run(), write_reports


# Order matters: ``fabric_mapping`` consumes JSON outputs from earlier
# modules, so it must run after them in any "all modules" execution.
MODULE_REGISTRY: dict[str, ModuleFactory] = {
    "dedicated_pools": _dedicated,
    "serverless_pools": _serverless,
    "spark_pools": _spark,
    "pipelines": _pipelines,
    "monitoring": _monitoring,
    "storage": _storage,
    "governance": _governance,
    "security": _security,
    "cost": _cost,
    "fabric_validation": _fabric_validation,
    "databricks_workflows": _databricks_workflows,
    "bigquery_workloads": _bigquery_workloads,
    "snowflake_workloads": _snowflake_workloads,
    "fabric_mapping": _fabric_mapping,
}

# Tuple form used by the web layer for canonical-order sorting.
KNOWN_MODULES: tuple[str, ...] = tuple(MODULE_REGISTRY)

__all__ = ["MODULE_REGISTRY", "KNOWN_MODULES", "ModuleFactory", "NullProgress"]
