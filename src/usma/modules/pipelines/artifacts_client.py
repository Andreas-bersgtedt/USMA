"""Synapse Artifacts API client (pipelines / linked services / datasets / triggers / IRs)."""
from __future__ import annotations

import logging
from typing import Any, Iterator

from azure.synapse.artifacts import ArtifactsClient
from azure.mgmt.synapse import SynapseManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from . import fabric_compat
from .models import Activity, Dataset, IntegrationRuntime, LinkedService, Pipeline, Trigger

log = logging.getLogger(__name__)


class ArtifactsApiClient:
    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._endpoint = f"https://{azure.workspace_name}.dev.azuresynapse.net"
        cred = get_credential(azure)
        self._artifacts = ArtifactsClient(endpoint=self._endpoint, credential=cred)
        # IRs come from ARM, not the artifacts plane.
        self._mgmt = SynapseManagementClient(cred, azure.subscription_id)

    @property
    def endpoint(self) -> str:
        return self._endpoint

    # ------------------------------------------------------------------ pipelines
    def iter_pipelines_with_activities(self) -> Iterator[tuple[Pipeline, list[Activity]]]:
        """Yield (pipeline, activities) pairs walking each pipeline's activity tree."""
        for p in self._artifacts.pipeline.get_pipelines_by_workspace():
            raw_activities = list(getattr(p, "activities", None) or [])
            flat: list[Activity] = []
            _walk_activities(p.name, raw_activities, flat)

            types = sorted({a.type for a in flat if a.type})
            unsupported = sum(1 for a in flat if a.support == "unsupported")
            partial = sum(1 for a in flat if a.support == "partial")
            yield (
                Pipeline(
                    name=p.name,
                    folder=getattr(getattr(p, "folder", None), "name", None),
                    activity_count=len(flat),
                    activity_types=types,
                    annotations=[str(a) for a in (getattr(p, "annotations", None) or [])],
                    unsupported_activity_count=unsupported,
                    partial_activity_count=partial,
                ),
                flat,
            )

    def list_linked_services(self) -> Iterator[LinkedService]:
        for ls in self._artifacts.linked_service.get_linked_services_by_workspace():
            props = getattr(ls, "properties", None)
            connect_via = getattr(getattr(props, "connect_via", None), "reference_name", None) if props else None
            ls_type = getattr(props, "type", None) or "Unknown"
            yield LinkedService(
                name=ls.name,
                type=ls_type,
                connect_via=connect_via,
                annotations=[str(a) for a in (getattr(props, "annotations", None) or [])],
                fabric_supported=fabric_compat.linked_service_supported(ls_type),
            )

    def list_datasets(self) -> Iterator[Dataset]:
        for ds in self._artifacts.dataset.get_datasets_by_workspace():
            props = getattr(ds, "properties", None)
            ls_ref = getattr(getattr(props, "linked_service_name", None), "reference_name", None) if props else None
            yield Dataset(
                name=ds.name,
                type=getattr(props, "type", None) or "Unknown",
                linked_service=ls_ref,
                folder=getattr(getattr(props, "folder", None), "name", None) if props else None,
            )

    def list_triggers(self) -> Iterator[Trigger]:
        for t in self._artifacts.trigger.get_triggers_by_workspace():
            props = getattr(t, "properties", None)
            pipelines = []
            for ref in (getattr(props, "pipelines", None) or []):
                p_ref = getattr(ref, "pipeline_reference", None)
                name = getattr(p_ref, "reference_name", None) if p_ref else None
                if name:
                    pipelines.append(name)
            # Pass the raw type-specific payload (recurrence, interval, etc.)
            # through so the schedule_mapper can render cadence.
            type_properties = None
            if props is not None and hasattr(props, "as_dict"):
                try:
                    type_properties = props.as_dict()
                except Exception:
                    type_properties = None
            yield Trigger(
                name=t.name,
                type=getattr(props, "type", None) or "Unknown",
                runtime_state=getattr(props, "runtime_state", None),
                pipelines=pipelines,
                type_properties=type_properties,
            )

    # ------------------------------------------------------------------ IRs (ARM)
    def list_integration_runtimes(self) -> Iterator[IntegrationRuntime]:
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        for ir in self._mgmt.integration_runtimes.list_by_workspace(rg, ws):
            props = getattr(ir, "properties", None)
            yield IntegrationRuntime(
                name=ir.name,
                type=getattr(props, "type", None) or "Unknown",
                description=getattr(props, "description", None),
            )


# ---------------------------------------------------------------- internals

def _walk_activities(pipeline_name: str, activities: list[Any], sink: list[Activity]) -> None:
    """Flatten control activities (ForEach/Until/IfCondition/Switch) into a single list."""
    for raw in activities:
        a_type = getattr(raw, "type", None) or raw.__class__.__name__
        a_name = getattr(raw, "name", None) or "<unnamed>"
        depends_on: list[str] = []
        for dep in (getattr(raw, "depends_on", None) or []):
            dep_name = getattr(dep, "activity", None)
            if dep_name:
                depends_on.append(dep_name)

        type_props = getattr(raw, "type_properties", None)

        # Reference detection — best-effort, attribute-based.
        pipeline_ref = None
        ds_ref = None
        ls_ref = None
        for src in (raw, type_props):
            if src is None:
                continue
            pr = getattr(src, "pipeline", None)
            if pr is not None:
                pipeline_ref = pipeline_ref or getattr(pr, "reference_name", None)
            for attr in ("dataset", "source_dataset", "sink_dataset"):
                ref = getattr(src, attr, None)
                if ref is not None:
                    ds_ref = ds_ref or getattr(ref, "reference_name", None)
            ls = getattr(src, "linked_service_name", None)
            if ls is not None:
                ls_ref = ls_ref or getattr(ls, "reference_name", None)

        compat = fabric_compat.analyze_activity(a_type, _props_as_dict(type_props))
        notes: list[str] = list(compat.caveats)  # legacy field — keep populated

        # ExecuteDataFlow: capture static cluster compute size so that
        # run_stats can derive vCore-hours from runtime statistics
        # (cluster cores × wall-clock activity duration) rather than
        # relying on the service-side billingReference.
        df_cores: int | None = None
        df_compute_type: str | None = None
        if a_type == "ExecuteDataFlow":
            df_cores, df_compute_type = _extract_dataflow_compute(_props_as_dict(type_props))

        sink.append(Activity(
            pipeline=pipeline_name,
            name=a_name,
            type=a_type,
            depends_on=depends_on,
            support=compat.tier,
            references_pipeline=pipeline_ref,
            references_dataset=ds_ref,
            references_linked_service=ls_ref,
            notes=notes,
            support_reasons=compat.reasons,
            support_caveats=compat.caveats,
            fabric_equivalent=compat.fabric_equivalent,
            migration_action=compat.migration_action,
            doc_url=compat.doc_url,
            dataflow_cores=df_cores,
            dataflow_compute_type=df_compute_type,
        ))

        # Recurse into common control containers.
        for child_attr in ("activities", "if_true_activities", "if_false_activities", "default_activities"):
            children = getattr(type_props, child_attr, None) if type_props else None
            if children:
                _walk_activities(pipeline_name, list(children), sink)
        # Switch cases
        cases = getattr(type_props, "cases", None) if type_props else None
        if cases:
            for case in cases:
                case_acts = getattr(case, "activities", None)
                if case_acts:
                    _walk_activities(pipeline_name, list(case_acts), sink)


def _props_as_dict(type_props: Any) -> dict[str, Any]:
    """Best-effort conversion of an activity's ``type_properties`` to a plain dict.

    The Synapse Artifacts SDK uses msrest models that expose ``as_dict()``; pure
    dicts (offline tests / fixtures) are returned as-is; everything else is
    flattened via ``vars()`` with one level of nested-attribute expansion.
    """
    if type_props is None:
        return {}
    if isinstance(type_props, dict):
        return type_props
    as_dict = getattr(type_props, "as_dict", None)
    if callable(as_dict):
        try:
            d = as_dict()
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
    try:
        out: dict[str, Any] = {}
        for k, v in vars(type_props).items():
            if k.startswith("_"):
                continue
            if hasattr(v, "as_dict") and callable(v.as_dict):
                try:
                    out[k] = v.as_dict()
                    continue
                except Exception:  # noqa: BLE001
                    pass
            out[k] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def _extract_dataflow_compute(props: dict[str, Any]) -> tuple[int | None, str | None]:
    """Read the static ``compute.coreCount`` / ``compute.computeType`` from an
    ``ExecuteDataFlow`` activity's typeProperties.

    Returns ``(cores, compute_type)`` where ``cores`` is ``None`` if the
    activity uses the default autoresolve IR or expresses ``coreCount`` as a
    pipeline expression (we cannot resolve those statically). Supported
    explicit values per the ADF/Synapse schema are 8, 16, 32, 48, 80, 144, 272.
    """
    compute = props.get("compute") if isinstance(props, dict) else None
    if not isinstance(compute, dict):
        return None, None
    raw_cores = compute.get("coreCount")
    raw_type = compute.get("computeType")
    cores: int | None = None
    if isinstance(raw_cores, bool):
        # bool is a subclass of int — exclude it explicitly.
        cores = None
    elif isinstance(raw_cores, int):
        cores = raw_cores
    elif isinstance(raw_cores, float):
        cores = int(raw_cores) if raw_cores.is_integer() else None
    elif isinstance(raw_cores, str):
        try:
            cores = int(raw_cores)
        except ValueError:
            cores = None  # pipeline expression — fall back to default at runtime
    compute_type = raw_type if isinstance(raw_type, str) else None
    return cores, compute_type
