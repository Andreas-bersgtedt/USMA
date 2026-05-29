"""Orchestrator for the spark_pools module."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from .arm_client import SparkArmClient
from .artifacts_client import SparkArtifactsClient
from .spark_history_client import SparkHistoryClient
from .spark_run_stats import aggregate_runs
from . import notebook_lint, runtime_compat
from .models import (
    NotebookLintFinding,
    RuntimeMappingResult,
    SparkAnalysis,
    SparkRunRecord,
)

log = logging.getLogger(__name__)


class SparkPoolsAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._arm = SparkArmClient(cfg.azure)
        self._artifacts = SparkArtifactsClient(cfg.azure)
        self._progress = progress or NullProgress()

    def run(self) -> SparkAnalysis:
        result = SparkAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            generated_at=datetime.now(timezone.utc),
        )
        # 7 sub-tasks: list_pools, list_notebooks, list_sjd, runtime_compat,
        # notebook_lint, libraries, spark_history (optional, gated by env var).
        self._progress.start(7, label="enumerating Spark assets")
        try:
            result.pools = list(self._arm.list_pools())
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to list Spark pools: %s", exc)
            result.errors.append(format_error("list_pools", exc))
        self._progress.step(label="pools")

        try:
            result.notebooks = list(self._artifacts.list_notebooks())
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to list notebooks: %s", exc)
            result.errors.append(format_error("notebooks", exc))
        self._progress.step(label="notebooks")

        try:
            result.spark_job_definitions = list(self._artifacts.list_spark_job_definitions())
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to list Spark job definitions: %s", exc)
            result.errors.append(format_error("spark_job_definitions", exc))
        self._progress.step(label="spark_job_definitions")

        # v2 — runtime compatibility table per pool.
        try:
            for pool in result.pools:
                m = runtime_compat.map_runtime(pool.spark_version)
                result.runtime_mappings.append(RuntimeMappingResult(
                    pool_name=pool.name,
                    synapse_version=pool.spark_version,
                    fabric_runtime=m.fabric_runtime,
                    fabric_spark=m.fabric_spark,
                    status=m.status,
                    note=m.note,
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning("runtime_compat failed: %s", exc)
            result.errors.append(f"runtime_compat: {exc}")
        self._progress.step(label="runtime_compat")

        # v2 — notebook lint. We use whatever source the artifacts client made available;
        # lint_notebooks tolerates missing source via the provider returning None.
        try:
            def _source_provider(nb_dict):
                # Notebook source is fetched lazily via the artifacts client — fail-soft.
                getter = getattr(self._artifacts, "fetch_notebook_source", None)
                if getter is None:
                    return None
                try:
                    return getter(nb_dict.get("name"))
                except Exception:  # noqa: BLE001
                    return None

            findings_by_nb = notebook_lint.lint_notebooks(
                [nb.model_dump() for nb in result.notebooks],
                _source_provider,
            )
            for nb_name, findings in findings_by_nb.items():
                for f in findings:
                    result.notebook_lint_findings.append(NotebookLintFinding(
                        notebook=nb_name,
                        rule_id=f.rule_id, label=f.label,
                        severity=f.severity, line=f.line, snippet=f.snippet,
                    ))
        except Exception as exc:  # noqa: BLE001
            log.warning("notebook_lint failed: %s", exc)
            result.errors.append(f"notebook_lint: {exc}")
        self._progress.step(label="notebook_lint")

        # v2 — workspace / pool library inventory. Stub: gracefully no-ops when the
        # artifacts SDK does not expose `list_workspace_packages` on this workspace.
        try:
            getter = getattr(self._artifacts, "list_workspace_packages", None)
            if callable(getter):
                result.libraries = list(getter())
        except Exception as exc:  # noqa: BLE001
            log.info("list_workspace_packages unavailable: %s", exc)
        self._progress.step(label="libraries")

        # v2 — Spark Livy job history (interactive sessions + scheduled batches).
        # Gated by SMA_SPARK_RUN_HISTORY (default on). Per-pool I/O is fanned out
        # with ThreadPoolExecutor to bound wall-clock on workspaces with many pools.
        if _env_flag("SMA_SPARK_RUN_HISTORY", default=True) and result.pools:
            days = _env_int("SMA_SPARK_RUN_DAYS", default=90, minimum=1)
            # The Livy job/session list APIs don't expose a server-side
            # time filter, so the client pages offset-based newest-first
            # and stops once a whole page is fully older than ``start``.
            # The ``limit`` is only a *safety* ceiling — bump it well above
            # the typical 5000-row truncation so production workspaces
            # collect a complete window. SMA_SPARK_RUN_LIMIT still wins
            # when explicitly overridden.
            limit = _env_int("SMA_SPARK_RUN_LIMIT", default=50000, minimum=1)
            page_size = _env_int("SMA_SPARK_RUN_PAGE_SIZE", default=20, minimum=1)
            concurrency = _env_int("SMA_SPARK_RUN_CONCURRENCY", default=4, minimum=1)
            start = datetime.now(timezone.utc) - timedelta(days=days)
            history = SparkHistoryClient(self._cfg.azure)
            collected: list[SparkRunRecord] = []

            # Per-pool progress: one sub-step per pool × {batch, session}.
            pool_names = [p.name for p in result.pools]
            self._progress.add_total(len(pool_names) * 2)
            self._progress.message(
                f"fetching Spark Livy history ({days} days) across "
                f"{len(pool_names)} pool{'s' if len(pool_names) != 1 else ''}"
            )

            def _fetch(pool_name: str) -> list[SparkRunRecord]:
                runs: list[SparkRunRecord] = []
                try:
                    runs.extend(history.iter_batch_jobs(
                        pool_name, start=start, limit=limit, page_size=page_size,
                    ))
                    self._progress.step(
                        1, label=f"spark batch history [{pool_name}]",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("spark batch history (%s) failed: %s", pool_name, exc)
                    result.errors.append(
                        format_error(f"spark_history_batches[{pool_name}]", exc)
                    )
                    self._progress.step(1)
                try:
                    runs.extend(history.iter_sessions(
                        pool_name, start=start, limit=limit, page_size=page_size,
                    ))
                    self._progress.step(
                        1, label=f"spark session history [{pool_name}]",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("spark session history (%s) failed: %s", pool_name, exc)
                    result.errors.append(
                        format_error(f"spark_history_sessions[{pool_name}]", exc)
                    )
                    self._progress.step(1)
                return runs

            try:
                with ThreadPoolExecutor(max_workers=concurrency) as ex:
                    futures = {ex.submit(_fetch, name): name for name in pool_names}
                    for fut in as_completed(futures):
                        try:
                            collected.extend(fut.result())
                        except Exception as exc:  # noqa: BLE001
                            log.warning("spark history fetch failed: %s", exc)
                            result.errors.append(format_error("spark_history", exc))
                result.spark_runs = collected
                result.run_stats = aggregate_runs(collected)
            except Exception as exc:  # noqa: BLE001
                log.warning("spark_history aggregation failed: %s", exc)
                result.errors.append(format_error("spark_history", exc))
        self._progress.step(label="spark_history")

        return result


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, *, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return max(minimum, n)
