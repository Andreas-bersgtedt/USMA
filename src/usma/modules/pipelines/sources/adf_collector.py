"""ADF ``PipelineCollector`` adapter — Phase 2 (structural).

Wraps :class:`azure.mgmt.datafactory.DataFactoryManagementClient` behind
the :class:`PipelineCollector` Protocol so the analyzer can dispatch
between Synapse and ADF without per-source branches.

**Status:** Phase 2 ships the **structural** version of this collector —
the SDK calls + the top-level model construction (Pipeline name, type,
folder; LinkedService name + type; etc.) are in place and unit-tested
against fake SDKs. The deep activity-payload adaptation
(``fabric_compat.analyze_activity`` integration, ExpressionFinding
extraction, ScheduleMapping derivation from ADF triggers) reuses the
existing Synapse code in
:mod:`usma.modules.pipelines.fabric_compat`
because ADF and Synapse pipelines share the same JSON activity schema —
this is the entire reason ADF was chosen as Phase 2's first add-on.

The remaining work is integration-fixture-driven and lands as a
follow-up PR once an ADF instance is available to record fixtures from.
"""
from __future__ import annotations

from typing import Any, Iterator

from ....sources.adf.provider import AdfClientBundle
from ..fabric_compat import analyze_activity  # reused across sources
from ..models import (
    Activity,
    Dataset,
    IntegrationRuntime,
    LinkedService,
    Pipeline,
    Trigger,
)


class AdfCollector:
    """Adapt ``DataFactoryManagementClient`` to the
    :class:`PipelineCollector` Protocol.

    The constructor takes an :class:`AdfClientBundle` (from
    :meth:`AdfProvider.make_clients`) and builds the management client
    lazily on first use to keep instantiation cheap.
    """

    def __init__(self, bundle: AdfClientBundle) -> None:
        self._bundle = bundle
        self._mgmt: Any = None

    @property
    def source_label(self) -> str:
        return self._bundle.factory_name

    @property
    def endpoint(self) -> str:
        """ARM ID of the ADF factory, used as the run-level provenance
        string in :attr:`PipelinesAnalysis.artifacts_endpoint` (the
        synapse ``dev.azuresynapse.net`` URL is meaningless for ADF)."""
        return (
            f"/subscriptions/{self._bundle.subscription_id}"
            f"/resourceGroups/{self._bundle.resource_group}"
            f"/providers/Microsoft.DataFactory/factories/{self._bundle.factory_name}"
        )

    @property
    def bundle(self) -> AdfClientBundle:
        """Expose the underlying credential/factory bundle so collateral
        clients (e.g. :class:`AdfRunHistoryClient`) can reuse it without
        re-resolving the descriptor."""
        return self._bundle

    # ------------------------------------------------------------------
    # SDK helper
    # ------------------------------------------------------------------
    def _client(self) -> Any:
        if self._mgmt is None:
            from azure.mgmt.datafactory import DataFactoryManagementClient

            self._mgmt = DataFactoryManagementClient(
                self._bundle.credential, self._bundle.subscription_id,
            )
        return self._mgmt

    def _factory_args(self) -> dict[str, str]:
        return {
            "resource_group_name": self._bundle.resource_group,
            "factory_name": self._bundle.factory_name,
        }

    # ------------------------------------------------------------------
    # PipelineCollector implementation
    # ------------------------------------------------------------------
    def iter_pipelines_with_activities(
        self,
    ) -> Iterator[tuple[Pipeline, list[Activity]]]:
        mgmt = self._client()
        for raw in mgmt.pipelines.list_by_factory(**self._factory_args()):
            pipeline_name = getattr(raw, "name", "") or ""
            folder = _folder_path(getattr(raw, "folder", None))
            raw_activities: list[Any] = list(getattr(raw, "activities", []) or [])
            activities = [
                _adapt_activity(a, pipeline_name=pipeline_name)
                for a in raw_activities
            ]
            pipeline = Pipeline(
                name=pipeline_name,
                folder=folder,
                activity_count=len(activities),
                activity_types=sorted({a.type for a in activities if a.type}),
                annotations=list(getattr(raw, "annotations", []) or []),
                unsupported_activity_count=sum(
                    1 for a in activities if a.support == "unsupported"
                ),
                partial_activity_count=sum(
                    1 for a in activities if a.support == "partial"
                ),
            )
            yield pipeline, activities

    def list_linked_services(self) -> Iterator[LinkedService]:
        mgmt = self._client()
        for raw in mgmt.linked_services.list_by_factory(**self._factory_args()):
            props = getattr(raw, "properties", None)
            name = getattr(raw, "name", "") or ""
            ls_type = getattr(props, "type", "") or "" if props else ""
            connect_via_ref = getattr(props, "connect_via", None) if props else None
            connect_via = (
                getattr(connect_via_ref, "reference_name", None)
                if connect_via_ref is not None
                else None
            )
            annotations = list(getattr(props, "annotations", []) or []) if props else []
            yield LinkedService(
                name=name,
                type=ls_type,
                connect_via=connect_via,
                annotations=annotations,
            )

    def list_datasets(self) -> Iterator[Dataset]:
        mgmt = self._client()
        for raw in mgmt.datasets.list_by_factory(**self._factory_args()):
            props = getattr(raw, "properties", None)
            name = getattr(raw, "name", "") or ""
            ds_type = getattr(props, "type", "") or "" if props else ""
            ls_ref = getattr(props, "linked_service_name", None) if props else None
            linked_service = (
                getattr(ls_ref, "reference_name", None) if ls_ref is not None else None
            )
            folder = _folder_path(getattr(props, "folder", None)) if props else None
            yield Dataset(
                name=name,
                type=ds_type,
                linked_service=linked_service,
                folder=folder,
            )

    def list_triggers(self) -> Iterator[Trigger]:
        mgmt = self._client()
        for raw in mgmt.triggers.list_by_factory(**self._factory_args()):
            props = getattr(raw, "properties", None)
            name = getattr(raw, "name", "") or ""
            t_type = getattr(props, "type", "") or "" if props else ""
            runtime_state = getattr(props, "runtime_state", None) if props else None
            # pipelines list shape: list of TriggerPipelineReference, each
            # with .pipeline_reference.reference_name
            refs = getattr(props, "pipelines", None) if props else None
            pipelines: list[str] = []
            for r in refs or []:
                pref = getattr(r, "pipeline_reference", None)
                rname = getattr(pref, "reference_name", None) if pref else None
                if rname:
                    pipelines.append(rname)
            # Pass through the raw type-specific payload (recurrence,
            # interval, etc.) so the schedule_mapper can resolve cadence
            # without re-querying the SDK.
            type_properties: dict[str, Any] | None = None
            if props is not None and hasattr(props, "as_dict"):
                try:
                    type_properties = props.as_dict()  # type: ignore[assignment]
                except Exception:
                    type_properties = None
            yield Trigger(
                name=name,
                type=t_type,
                runtime_state=runtime_state,
                pipelines=pipelines,
                type_properties=type_properties,
            )

    def list_integration_runtimes(self) -> Iterator[IntegrationRuntime]:
        mgmt = self._client()
        for raw in mgmt.integration_runtimes.list_by_factory(**self._factory_args()):
            props = getattr(raw, "properties", None)
            name = getattr(raw, "name", "") or ""
            ir_type = getattr(props, "type", "") or "" if props else ""
            description = getattr(props, "description", None) if props else None
            yield IntegrationRuntime(
                name=name, type=ir_type, description=description,
            )


# ---------------------------------------------------------------------------
# Internals — kept module-level so tests can exercise them without
# constructing the SDK client.
# ---------------------------------------------------------------------------


def _folder_path(folder: Any) -> str | None:
    """ADF folders are nested objects with a ``name`` attribute."""
    if folder is None:
        return None
    return getattr(folder, "name", None)


def _adapt_activity(raw: Any, *, pipeline_name: str) -> Activity:
    """Adapt a raw ADF activity object to the analyzer's
    :class:`Activity` model.

    Delegates compatibility classification to the shared
    :func:`fabric_compat.analyze_activity` helper, which works
    identically for Synapse and ADF because both serialize activities
    using the same Azure Data Factory activity JSON schema.
    """
    act_name = getattr(raw, "name", "") or ""
    act_type = getattr(raw, "type", "") or ""
    depends_on_refs = list(getattr(raw, "depends_on", []) or [])
    depends_on = [
        getattr(d, "activity", None) or ""
        for d in depends_on_refs
    ]
    depends_on = [d for d in depends_on if d]

    # ``analyze_activity`` takes the activity's ``typeProperties`` dict;
    # the ADF SDK exposes them as ``additional_properties`` (or model
    # fields depending on the activity class). Use whichever is present.
    type_properties: dict[str, Any] = {}
    add_props = getattr(raw, "additional_properties", None)
    if isinstance(add_props, dict):
        type_properties = add_props.get("typeProperties", {}) or {}
    # Some SDK activity classes expose typed fields directly; fall back
    # to constructing a dict from the model's ``as_dict()`` if needed.
    if not type_properties and hasattr(raw, "as_dict"):
        try:
            full = raw.as_dict()
            type_properties = full.get("typeProperties", {}) or {}
        except Exception:  # noqa: BLE001
            type_properties = {}

    detail = analyze_activity(act_type, type_properties)

    return Activity(
        pipeline=pipeline_name,
        name=act_name,
        type=act_type,
        depends_on=depends_on,
        support=detail.tier,  # type: ignore[arg-type]
        support_reasons=detail.reasons,
        support_caveats=detail.caveats,
        fabric_equivalent=detail.fabric_equivalent,
        migration_action=detail.migration_action,
        doc_url=detail.doc_url,
    )


__all__ = ["AdfCollector"]
