"""In-process analyzer run orchestrator for the local web control plane.

The runner re-uses the same Analyzer + write_reports functions the CLI
uses; nothing about the analysis logic moves into ``web/``. Each run is
serialised by an ``asyncio.Lock`` (analyzer is IO-heavy and external
APIs are rate-limited) and runs in a worker thread so it doesn't block
the event loop.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses as _dc
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig, load_config
from ..modules import MODULE_REGISTRY
from ..progress import CallbackProgress, ProgressReporter
from .schemas import KNOWN_MODULES, ModuleStatus, RunMeta, ScopeRef
from .storage import FilesystemRunRepo

log = logging.getLogger(__name__)


def _module_dispatch() -> dict[str, Callable[[AppConfig, ProgressReporter], tuple[Any, Callable[..., list[Path]]]]]:
    """Return the analyzer dispatch table.

    Thin wrapper around :data:`..modules.MODULE_REGISTRY` so callers (and
    tests) can pass a custom ``dispatch=`` to ``JobRunner.__init__`` while
    the production default stays in lockstep with the CLI.
    """
    return dict(MODULE_REGISTRY)


def _scope_is_azure(scope: ScopeRef | None) -> bool:
    """True when the scope's primary source lives in Azure.

    BigQuery is always GCP; Snowflake / Databricks carry their
    hyperscaler in ``extras['platform']`` (``aws``/``gcp``/``azure``).
    Synapse and ADF scopes are Azure-only by construction.
    """
    if scope is None:
        return False
    st = (scope.source_type or "").lower()
    if st == "bigquery":
        return False
    if st in {"databricks", "snowflake"}:
        plat = str((scope.extras or {}).get("platform", "")).lower()
        # Databricks defaults to Azure when no platform hint is present
        # (legacy single-scope path); Snowflake defaults to AWS.
        if not plat:
            return st == "databricks"
        return plat == "azure"
    # synapse_workspace, adf, sap_bw → Azure.
    return True


def migrate_run_attribution(
    runs_dir: Path, *, dry_run: bool = False
) -> dict[str, list[str]]:
    """Strip Azure identity from existing ``run.json`` files for non-Azure scopes.

    Older runs (created before the cloud-aware ``RunMeta`` stamping in
    :meth:`JobRunner.start`) inherited ``AZURE_TENANT_ID`` /
    ``AZURE_SUBSCRIPTION_ID`` / ``SYNAPSE_RESOURCE_GROUP`` /
    ``SYNAPSE_WORKSPACE_NAME`` from the service-principal credentials
    even when the run's primary scope was BigQuery, Snowflake-on-AWS,
    or Databricks-on-AWS/GCP. This rewrites those four fields to match
    the new convention so the Estate Overview groups them under the
    correct hyperscaler bucket.

    Returns ``{"updated": [...], "skipped": [...], "errors": [...]}``.
    Pass ``dry_run=True`` to compute the list without touching disk.
    """
    runs_dir = runs_dir.resolve()
    out: dict[str, list[str]] = {"updated": [], "skipped": [], "errors": []}
    if not runs_dir.is_dir():
        return out
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        run_json = child / "run.json"
        if not run_json.is_file():
            continue
        try:
            meta = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out["errors"].append(f"{child.name}: {exc}")
            continue
        scopes = meta.get("scopes") or []
        primary = scopes[0] if isinstance(scopes, list) and scopes else None
        if not isinstance(primary, dict):
            out["skipped"].append(child.name)
            continue
        scope = ScopeRef(
            source_type=primary.get("source_type") or "synapse_workspace",
            id=primary.get("id") or "x",
            display_name=primary.get("display_name") or "x",
            subscription_id=primary.get("subscription_id"),
            resource_group=primary.get("resource_group"),
            extras=dict(primary.get("extras") or {}),
        )
        if _scope_is_azure(scope):
            out["skipped"].append(child.name)
            continue
        # Non-Azure scope: zero out inherited Azure identity. Prefer the
        # scope's own ``display_name`` for the workspace name so the
        # Estate Overview shows something meaningful (e.g. GCP project
        # id) rather than the unrelated Synapse workspace name from
        # ``.env``.
        changed = False
        for field in ("tenant_id", "subscription_id", "resource_group"):
            if meta.get(field) is not None:
                meta[field] = None
                changed = True
        new_ws = scope.display_name or None
        if meta.get("workspace_name") != new_ws:
            meta["workspace_name"] = new_ws
            changed = True
        if not changed:
            out["skipped"].append(child.name)
            continue
        if not dry_run:
            tmp = run_json.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(meta, indent=2, sort_keys=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(run_json)
        out["updated"].append(child.name)
    return out


def hash_config(cfg: AppConfig) -> str:
    """sha256 of the redacted AppConfig (secret excluded)."""
    payload = {
        "tenant_id": cfg.azure.tenant_id,
        "client_id": cfg.azure.client_id,
        "subscription_id": cfg.azure.subscription_id,
        "resource_group": cfg.azure.resource_group,
        "workspace_name": cfg.azure.workspace_name,
        "dedicated_pool": cfg.azure.dedicated_pool,
        "odbc_driver": cfg.sql.odbc_driver,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


class JobRunner:
    """Single-runner queue. Only one analyzer run executes at a time."""

    def __init__(
        self,
        repo: FilesystemRunRepo,
        *,
        env_file: Path | None = None,
        dispatch: dict[str, Callable[[AppConfig, ProgressReporter], tuple[Any, Callable[..., list[Path]]]]] | None = None,
        load_cfg: Callable[[Path | None], AppConfig] | None = None,
    ) -> None:
        self.repo = repo
        self.env_file = env_file
        self._dispatch = dispatch or _module_dispatch()
        self._load_cfg = load_cfg or load_config
        self._lock = asyncio.Lock()
        # ``_busy`` is the authoritative "a run is in flight" flag. It is
        # mutated only from the asyncio thread and is set *before* the
        # background task is scheduled to close the TOCTOU race that
        # checking ``self._lock.locked()`` had: ``_lock`` is acquired
        # inside ``_run`` which is scheduled via ``create_task`` and
        # therefore not yet acquired when ``start`` returns.
        self._busy = False
        self._cancel: dict[str, threading.Event] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict]]] = {}
        self._sub_lock = threading.Lock()
        # Per-run flag tracking whether we have already emitted a
        # ``lost_events`` sentinel for the live SSE channel after the
        # subscriber queue overflowed; the persisted log has its own
        # cap enforced by :class:`FilesystemRunRepo`.
        self._lost_emitted: set[str] = set()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def known_modules(self) -> tuple[str, ...]:
        return KNOWN_MODULES

    # ------------------------------------------------------------------
    # Subscribe / publish (used by the SSE endpoint)
    # ------------------------------------------------------------------

    def subscribe(self, run_id: str) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        with self._sub_lock:
            self._subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[dict]) -> None:
        with self._sub_lock:
            subs = self._subscribers.get(run_id, [])
            if q in subs:
                subs.remove(q)
            if not subs and run_id in self._subscribers:
                del self._subscribers[run_id]

    def _publish(self, run_id: str, event: dict) -> None:
        # Persist for replay-on-reconnect.
        try:
            self.repo.append_event(run_id, event)
        except Exception:  # noqa: BLE001
            log.exception("failed to persist event for %s", run_id)
        # Fan out to live subscribers; if any queue is full, emit a single
        # ``lost_events`` marker so the SPA can surface a banner instead of
        # silently missing progress updates.
        with self._sub_lock:
            subs = list(self._subscribers.get(run_id, ()))
        any_dropped = False
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                any_dropped = True
        if any_dropped and run_id not in self._lost_emitted:
            self._lost_emitted.add(run_id)
            sentinel = {
                "type": "lost_events",
                "reason": "subscriber_queue_full",
                "run_id": run_id,
            }
            with self._sub_lock:
                subs2 = list(self._subscribers.get(run_id, ()))
            for q in subs2:
                try:
                    q.put_nowait(sentinel)
                except asyncio.QueueFull:
                    log.warning("SSE queue full; dropping lost_events sentinel for %s", run_id)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def cancel(self, run_id: str) -> bool:
        ev = self._cancel.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    # ------------------------------------------------------------------
    # Carry-forward
    # ------------------------------------------------------------------

    # Per-module artefact extensions that should be copied forward.
    # ``json`` is the load-bearing one (the SPA consumes it); the others
    # are convenience renderings used by users browsing the on-disk dir.
    _CARRY_EXTENSIONS = ("json", "csv", "md", "html")

    def _carry_forward_from_prior(self, meta: RunMeta, run_dir: Path) -> None:
        """Copy module artefacts from the most recent workspace-matched run.

        Modules already in ``meta.modules`` (i.e. selected for this run)
        are skipped because this run is going to produce them. For every
        other module name in :data:`KNOWN_MODULES`, if the prior run has
        a ``<module>.json`` on disk, copy it (and any sibling csv/md/html)
        into ``run_dir`` and record the inheritance on ``meta``.

        Stamps each carried module onto ``meta.modules`` with
        ``state == "carried"`` and ``carried_from_run_id`` set to the
        source run, plus records the mapping on ``meta.carried_from``.

        Mutates ``meta`` in place and persists it via ``self.repo.update``.
        Best-effort: any per-file copy error is logged and skipped.
        """
        prior = self.repo.latest_for_workspace(
            tenant_id=meta.tenant_id,
            subscription_id=meta.subscription_id,
            resource_group=meta.resource_group,
            workspace_name=meta.workspace_name,
            exclude_id=meta.id,
        )
        if prior is None:
            return
        try:
            prior_dir = self.repo.run_dir(prior.id)
        except ValueError:
            return
        if not prior_dir.exists():
            return

        selected = {ms.name for ms in meta.modules}
        # For each module on the prior run, the *true* source is either
        # the prior run itself (if it executed the module) or — when the
        # prior run also carried the module forward — the original
        # producer pointed at by ``prior.carried_from``. This keeps the
        # chain from elongating across many partial runs.
        prior_module_by_name = {ms.name: ms for ms in prior.modules}

        carried: list[str] = []
        for module in KNOWN_MODULES:
            if module in selected:
                continue
            src_json = prior_dir / f"{module}.json"
            if not src_json.exists():
                continue
            # Resolve the original producer.
            source_run_id = prior.carried_from.get(module, prior.id)
            prior_ms = prior_module_by_name.get(module)
            if prior_ms is not None and prior_ms.state == "carried":
                source_started_at = prior_ms.carried_from_started_at
            elif prior_ms is not None:
                source_started_at = prior_ms.started_at or prior.started_at
            else:
                source_started_at = prior.started_at

            # Copy every available rendering. ``copy2`` preserves mtime so
            # the carried-over artefact still reports its original age.
            for ext in self._CARRY_EXTENSIONS:
                src = prior_dir / f"{module}.{ext}"
                if not src.exists():
                    continue
                dst = run_dir / src.name
                try:
                    shutil.copy2(src, dst)
                except OSError as exc:
                    log.warning(
                        "carry-forward: could not copy %s from run %s: %s",
                        src.name, prior.id, exc,
                    )
            if not (run_dir / f"{module}.json").exists():
                # Copy failed for the load-bearing file; don't claim a
                # carry happened.
                continue
            carried.append(module)
            meta.modules.append(ModuleStatus(
                name=module,
                state="carried",
                carried_from_run_id=source_run_id,
                carried_from_started_at=source_started_at,
            ))
            meta.carried_from[module] = source_run_id

        if carried:
            log.info(
                "run %s: carried %d module artefact(s) forward from run %s: %s",
                meta.id, len(carried), prior.id, ", ".join(carried),
            )
            self.repo.update(meta)
            self._publish(meta.id, {
                "type": "modules_carried",
                "source_run_id": prior.id,
                "modules": carried,
            })

    async def start(
        self,
        modules: list[str],
        *,
        label: str | None = None,
        days: int | None = None,
        scopes: list[ScopeRef] | None = None,
    ) -> RunMeta:
        unknown = [m for m in modules if m not in self._dispatch]
        if unknown:
            raise ValueError(f"unknown modules: {unknown}. Known: {sorted(self._dispatch)}")

        # Atomically claim the runner: asyncio is single-threaded so the
        # check + assignment cannot interleave with another coroutine until
        # the next ``await``.
        if self._busy:
            raise RuntimeError("a run is already in flight")
        self._busy = True
        try:
            # Sort selected modules into canonical execution order so
            # `fabric_mapping` (which reads upstream JSON) always runs after
            # its inputs, regardless of the order the SPA submitted them.
            canonical = list(KNOWN_MODULES)
            modules = sorted(set(modules), key=canonical.index)

            # Build initial RunMeta and persist it before we kick off the work
            # so the API can return the id immediately.
            cfg = self._load_cfg(self.env_file)

            # When the caller did not pass an explicit scopes list (legacy
            # single-scope Run page), synthesize one from ``cfg.scopes`` so
            # downstream consumers (Estate overview, diff per-scope view)
            # can always rely on ``run.json.scopes[0].source_type`` being
            # populated. The wire type ``ScopeRef`` mirrors the persistence
            # ``SourceDescriptor`` shape.
            effective_scopes: list[ScopeRef] = list(scopes or [])
            cfg_scopes = getattr(cfg, "scopes", None) or ()
            if not effective_scopes and cfg_scopes:
                primary = cfg_scopes[0]
                effective_scopes = [
                    ScopeRef(
                        source_type=str(primary.type),  # type: ignore[arg-type]
                        id=primary.id,
                        display_name=primary.display_name,
                        subscription_id=primary.subscription_id,
                        resource_group=primary.resource_group,
                        # Phase 4.7 — forward the descriptor's extras so
                        # the cloud discriminator (``platform=aws|gcp``
                        # for Databricks) reaches run.json and the
                        # Estate Overview can bucket multi-cloud
                        # Databricks scopes under the right hyperscaler.
                        extras=dict(getattr(primary, "extras", {}) or {}),
                    )
                ]

            # Only stamp the Azure tenant / subscription / RG / workspace
            # identity onto the run when the primary scope actually lives
            # in Azure. BigQuery, Snowflake-on-non-Azure, and
            # Databricks-on-AWS/GCP scopes leave those fields null so
            # the Estate Overview doesn't mis-attribute them to whatever
            # Azure values happen to be sitting in ``.env`` for the
            # service-principal credentials.
            primary_scope = effective_scopes[0] if effective_scopes else None
            scope_is_azure = _scope_is_azure(primary_scope)
            meta = RunMeta(
                id=self.repo.new_id(),
                label=label,
                status="queued",
                started_at=datetime.now(timezone.utc),
                config_hash=hash_config(cfg),
                modules=[ModuleStatus(name=m, state="queued") for m in modules],
                tenant_id=cfg.azure.tenant_id if scope_is_azure else None,
                subscription_id=(
                    cfg.azure.subscription_id if scope_is_azure else None
                ),
                resource_group=(
                    cfg.azure.resource_group if scope_is_azure else None
                ),
                workspace_name=(
                    cfg.azure.workspace_name
                    if scope_is_azure
                    else (primary_scope.display_name if primary_scope else None)
                ),
                scopes=effective_scopes,
            )
            self.repo.create(meta)
            self._cancel[meta.id] = threading.Event()

            # Spawn the actual work. ``_busy`` will be cleared by ``_run``.
            loop = asyncio.get_running_loop()
            loop.create_task(self._run(meta, cfg, days=days))
            return meta
        except BaseException:
            # Failed to schedule — release the slot so subsequent attempts work.
            self._busy = False
            raise

    async def _run(self, meta: RunMeta, cfg: AppConfig, *, days: int | None = None) -> None:
        run_id = meta.id
        # Apply the per-run global lookback window by mutating os.environ
        # before the analyzers spin up. We restore the previous values in
        # the ``finally`` block so subsequent runs aren't affected.
        env_overrides: dict[str, str] = {}
        if days is not None and days > 0:
            env_overrides = {
                "SMA_PIPELINES_RUN_DAYS": str(days),
                "SMA_SPARK_RUN_DAYS": str(days),
                "SMA_MONITORING_DAYS": str(days),
            }
        prev_env: dict[str, str | None] = {}
        for k, v in env_overrides.items():
            prev_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            async with self._lock:
                meta.status = "running"
                self.repo.update(meta)
                self._publish(run_id, {"type": "run_started", "run_id": run_id})

                cancel = self._cancel[run_id]
                run_dir = self.repo.run_dir(run_id)
                # The fabric_mapping analyzer reads its inputs from
                # cfg.output_dir, so point the config at run_dir for the
                # duration of this run. ``AppConfig`` is a frozen dataclass
                # in production; tests may pass a non-dataclass mock, so
                # fall back to attribute assignment in that case.
                import dataclasses as _dc
                if _dc.is_dataclass(cfg) and not isinstance(cfg, type):
                    cfg = _dc.replace(cfg, output_dir=run_dir)
                else:
                    try:
                        cfg.output_dir = run_dir  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        log.warning("could not override output_dir on cfg %r", type(cfg))

                # Carry-forward: copy every module artefact that this run is
                # NOT going to (re)produce from the most recent completed
                # run for the same workspace identity. Lets a user refresh
                # one module at a time without losing the rest of the
                # workspace view. Best-effort — failures are logged and
                # the run continues (no carry is better than a half-broken
                # run dir).
                self._carry_forward_from_prior(meta, run_dir)

                partial_failure = False

            for ms in list(meta.modules):
                # Carry-forward entries are already terminal (state ==
                # "carried"). They were added in ``_carry_forward_from_prior``
                # and have no analyzer to execute; skip them so the
                # state machine isn't reset to ``running``.
                if ms.state == "carried":
                    continue
                if cancel.is_set():
                    self.repo.update_module(run_id, ms.name, state="cancelled")
                    self._publish(run_id, {
                        "type": "module_finished", "module": ms.name, "state": "cancelled",
                    })
                    continue

                self.repo.update_module(run_id, ms.name, state="running")
                self._publish(run_id, {"type": "module_started", "module": ms.name})
                t0 = time.monotonic()

                # Build a throttled progress reporter that fans events out to
                # SSE subscribers as ``module_progress`` events. Worker thread
                # invocations are marshalled back to the event loop via
                # ``call_soon_threadsafe`` so ``_publish`` (and the queue
                # mutations it does) only run on the asyncio thread.
                loop = asyncio.get_running_loop()
                module_name = ms.name

                # Throttle: at most one ``step`` event every 250 ms, plus
                # always emit on start / total grow / completion / message.
                last_emit_ts = [0.0]
                throttle_lock = threading.Lock()

                def _emit(snap: dict[str, Any]) -> None:
                    cur = int(snap.get("current", 0))
                    tot = int(snap.get("total", 0))
                    msg = snap.get("message")
                    label = snap.get("label") or ""
                    now = time.monotonic()
                    with throttle_lock:
                        # Always emit when total changes, on completion,
                        # when a fresh message arrives, or when the throttle
                        # window has elapsed.
                        elapsed = now - last_emit_ts[0]
                        terminal = tot > 0 and cur >= tot
                        if (
                            elapsed >= 0.25
                            or terminal
                            or msg is not None
                            or last_emit_ts[0] == 0.0
                        ):
                            last_emit_ts[0] = now
                        else:
                            return
                    payload = {
                        "type": "module_progress",
                        "module": module_name,
                        "current": cur,
                        "total": tot,
                        "label": label,
                        "message": msg,
                    }
                    try:
                        loop.call_soon_threadsafe(self._publish, run_id, payload)
                    except RuntimeError:
                        # Event loop closed mid-run (e.g. shutdown) — drop.
                        pass
                    # Persist the latest snapshot on RunMeta so
                    # ``GET /api/runs`` reflects progress for in-flight runs.
                    try:
                        self.repo.update_module_progress(
                            run_id, module_name,
                            current=cur, total=tot,
                            label=label or None, message=msg,
                        )
                    except Exception:  # noqa: BLE001
                        pass

                progress = CallbackProgress(_emit)

                try:
                    factory = self._dispatch[ms.name]
                    # Run analyzer + write_reports in a thread pool so we
                    # don't block the event loop. write_reports persists
                    # JSON / CSV / Markdown / HTML next to run_dir.
                    if _is_multi_scope(meta):
                        # Phase 2.7 — per-scope dispatch. Each module
                        # runs once per scope; artefacts land in a
                        # sub-directory ``<source_type>__<slug>`` so
                        # downstream loaders / diff can attribute them
                        # to the originating scope. Single-scope runs
                        # keep the legacy flat layout (else branch).
                        for scope in meta.scopes:
                            scope_dir = run_dir / f"{scope.source_type}__{_scope_slug(scope)}"
                            scope_dir.mkdir(parents=True, exist_ok=True)
                            scope_cfg = _cfg_for_scope(cfg, scope, scope_dir)
                            restore = _stamp_source_type_env(scope.source_type)
                            try:
                                await asyncio.to_thread(
                                    _run_module_sync, factory, scope_cfg, scope_dir, progress,
                                )
                            finally:
                                restore()
                    else:
                        await asyncio.to_thread(
                            _run_module_sync, factory, cfg, run_dir, progress,
                        )
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    self.repo.update_module(
                        run_id, ms.name, state="ok", duration_ms=duration_ms,
                    )
                    self._publish(run_id, {
                        "type": "module_finished",
                        "module": ms.name,
                        "state": "ok",
                        "duration_ms": duration_ms,
                    })
                except Exception as exc:  # noqa: BLE001
                    log.exception("module %s failed in run %s", ms.name, run_id)
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    self.repo.update_module(
                        run_id, ms.name, state="failed",
                        error=str(exc), duration_ms=duration_ms,
                    )
                    self._publish(run_id, {
                        "type": "module_finished",
                        "module": ms.name,
                        "state": "failed",
                        "error": str(exc),
                        "duration_ms": duration_ms,
                    })
                    partial_failure = True

            # Final RunMeta state.
            final = self.repo.get(run_id) or meta
            final.finished_at = datetime.now(timezone.utc)
            final.errors_count = sum(1 for m in final.modules if m.state == "failed")
            if cancel.is_set():
                final.status = "cancelled"
            elif partial_failure:
                final.status = "failed"
            else:
                final.status = "ok"
            # Reflect readiness from fabric_mapping if present.
            fm = run_dir / "fabric_mapping.json"
            if fm.exists():
                try:
                    data = json.loads(fm.read_text(encoding="utf-8"))
                    score = data.get("readiness", {}).get("score")
                    if isinstance(score, (int, float)):
                        final.readiness_score = float(score)
                except Exception:  # noqa: BLE001
                    pass
            self.repo.update(final)
            self._publish(run_id, {
                "type": "done",
                "status": final.status,
                "errors_count": final.errors_count,
                "readiness_score": final.readiness_score,
            })
            # Clean up the cancel event.
            self._cancel.pop(run_id, None)
        finally:
            # Release the runner slot whether the run finished cleanly or
            # raised. ``start`` will refuse new runs until this clears.
            self._busy = False
            self._lost_emitted.discard(run_id)
            # Restore any SMA_*_DAYS env vars we overrode for this run so
            # subsequent runs (or other code in the same process) see the
            # original values.
            for k, prev in prev_env.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev


def _run_module_sync(
    factory: Callable[[AppConfig, ProgressReporter], tuple[Any, Callable[..., list[Path]]]],
    cfg: AppConfig,
    run_dir: Path,
    progress: ProgressReporter,
) -> AsyncIterator[None]:
    """Helper: invoke the module's analyzer + writer with the run dir.

    Run synchronously; called via ``asyncio.to_thread``.
    """
    result, writer = factory(cfg, progress)
    # Every analyzer's writer accepts (result, output_dir, formats=...).
    writer(result, run_dir, formats=["json", "csv", "markdown", "html"])
    return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Phase 2.7 — multi-scope dispatch helpers
# ---------------------------------------------------------------------------
#
# When a run targets more than one scope (``len(meta.scopes) > 1``) the
# runner falls into a per-scope inner loop: each module executes once
# per scope, with its artefacts landing in a scope sub-directory under
# ``run_dir`` named ``<source_type>__<slug>``. Single-scope runs (the
# legacy path) are unchanged — they still write directly to ``run_dir``
# so existing loaders and tests keep working.
#
# These helpers are isolated so the rest of the orchestrator (carry-
# forward, progress, cancellation, SSE) stays untouched.


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _scope_slug(scope: ScopeRef) -> str:
    """Filesystem-safe lowercase slug derived from the scope display name.

    Mirrors the ``short_scope_id`` convention from D8 — readable, not
    a hash, so directory listings are greppable. Falls back to ``scope``
    when display_name / id are both empty.
    """
    name = (scope.display_name or scope.id or "scope").strip().lower()
    slug = _SLUG_RE.sub("-", name).strip("-")
    return slug or "scope"


def _cfg_for_scope(cfg: Any, scope: ScopeRef, scope_dir: Path) -> Any:
    """Materialize a per-scope ``AppConfig`` clone.

    Replaces ``azure.workspace_name`` / ``azure.resource_group`` with
    the scope's identity so module analyzers see the right targets, and
    points ``output_dir`` at the per-scope sub-directory so each module's
    writer lands artefacts in the correct place.

    Works for both production ``AppConfig`` dataclasses and the
    ``SimpleNamespace`` fakes used in tests — dataclass branch uses
    ``dataclasses.replace`` (which respects ``frozen=True``); namespace
    branch falls back to ``copy.copy`` + attribute assignment.
    """
    workspace = (
        scope.extras.get("workspace_name")
        or scope.extras.get("factory_name")
        or scope.display_name
    )
    rg = scope.resource_group or getattr(cfg.azure, "resource_group", None)

    if _dc.is_dataclass(cfg) and not isinstance(cfg, type):
        azure = _dc.replace(cfg.azure, resource_group=rg, workspace_name=workspace)
        return _dc.replace(cfg, azure=azure, output_dir=scope_dir)

    # Fake / mock cfg path used by tests.
    new_cfg = copy.copy(cfg)
    new_azure = copy.copy(cfg.azure)
    try:
        new_azure.resource_group = rg
        new_azure.workspace_name = workspace
    except Exception:  # noqa: BLE001
        pass
    new_cfg.azure = new_azure
    try:
        new_cfg.output_dir = scope_dir
    except Exception:  # noqa: BLE001
        pass
    return new_cfg


def _stamp_source_type_env(source_type: str) -> Callable[[], None]:
    """Set ``SMA_SOURCE_TYPE`` for the duration of a per-scope module.

    Returns a callable that restores the previous env value (or pops
    the key entirely). Used to ensure ``modules.__init__`` dispatch picks
    the correct provider for each scope inside a multi-scope run.
    """
    prev = os.environ.get("SMA_SOURCE_TYPE")
    os.environ["SMA_SOURCE_TYPE"] = source_type

    def _restore() -> None:
        if prev is None:
            os.environ.pop("SMA_SOURCE_TYPE", None)
        else:
            os.environ["SMA_SOURCE_TYPE"] = prev

    return _restore


def _is_multi_scope(meta: RunMeta) -> bool:
    """Whether this run should use the per-scope dispatch layout."""
    return len(meta.scopes) > 1
