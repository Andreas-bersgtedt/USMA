"""Phase 2 — analyzer dispatch integration test.

Verifies :class:`PipelinesAnalyzer` is source-agnostic: a fake ADF
collector (loaded from ``tests/fixtures/sources/adf/factory.json``) is
injected via the new ``collector=`` kwarg, and the analyzer produces a
valid :class:`PipelinesAnalysis` with ADF artifacts populated and the
Synapse-specific run-history step skipped.

This locks in the contract that landed the analyzer dispatch + ADF
fixture (see Phase 2 backend checklist in ``USMA_planning_Manifest.md``)
without needing real Azure credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.pipelines.analyzer import PipelinesAnalyzer
from usma.modules.pipelines.models import (
    Activity,
    Dataset,
    IntegrationRuntime,
    LinkedService,
    Pipeline,
    Trigger,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "adf" / "factory.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


class _FakeAdfCollector:
    """``PipelineCollector`` impl backed by the static ADF fixture.

    Hand-rolled (not built from the real ``AdfCollector``) so the test
    doesn't depend on ``DataFactoryManagementClient`` paging semantics.
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    @property
    def source_label(self) -> str:
        return self._data["factory"]["name"]

    def iter_pipelines_with_activities(self) -> Iterator[tuple[Pipeline, list[Activity]]]:
        for p in self._data["pipelines"]:
            acts = [
                Activity(
                    pipeline=p["name"],
                    name=a["name"],
                    type=a["type"],
                    depends_on=[d["activity"] for d in a.get("depends_on", [])],
                )
                for a in p.get("activities", [])
            ]
            pipe = Pipeline(
                name=p["name"],
                folder=(p.get("folder") or {}).get("name"),
                activity_count=len(acts),
                activity_types=sorted({a.type for a in acts}),
            )
            yield pipe, acts

    def list_linked_services(self) -> Iterator[LinkedService]:
        for ls in self._data["linked_services"]:
            yield LinkedService(name=ls["name"], type=ls["type"])

    def list_datasets(self) -> Iterator[Dataset]:
        for ds in self._data["datasets"]:
            yield Dataset(
                name=ds["name"],
                type=ds["type"],
                linked_service=ds.get("linked_service_name"),
            )

    def list_triggers(self) -> Iterator[Trigger]:
        for t in self._data["triggers"]:
            yield Trigger(name=t["name"], type=t["type"])

    def list_integration_runtimes(self) -> Iterator[IntegrationRuntime]:
        for ir in self._data["integration_runtimes"]:
            yield IntegrationRuntime(name=ir["name"], type=ir["type"])


@pytest.fixture
def fake_cfg() -> AppConfig:
    """Minimal AppConfig with stub Azure creds.

    The credentials are never used because the test injects a fake
    collector; SDK clients are constructed lazily and only touched when
    the Synapse code path is exercised.
    """
    return AppConfig(
        azure=AzureConfig(
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            subscription_id="sub",
            resource_group="rg",
            workspace_name="ws-unused",
        ),
        sql=SqlConfig(),
        output_dir=Path("./output"),
    )


def test_analyzer_dispatches_to_injected_collector(
    fake_cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a fake ADF collector and assert the resulting analysis is
    populated from the fixture (not the Synapse Artifacts API)."""
    collector = _FakeAdfCollector(_fixture())

    # Phase 2.6: run-history is now wired for both sources. The fake
    # collector in this test isn't an ``AdfCollector`` instance, so the
    # analyzer would fall through to the Synapse ``RunHistoryClient``
    # path and hit Azure. Disable run-history at the env-flag boundary
    # to keep this test offline; the dedicated ADF run-history test
    # below exercises the dispatch.
    monkeypatch.setenv("SMA_PIPELINES_RUN_HISTORY", "0")

    # The analyzer still constructs an ArtifactsApiClient eagerly in
    # __init__ to expose ``endpoint`` on the result. Patch it out so
    # azure.identity / azure.mgmt.synapse aren't actually invoked.
    with patch(
        "usma.modules.pipelines.analyzer.ArtifactsApiClient"
    ) as mock_api_cls:
        mock_api_cls.return_value.endpoint = "https://fixture.dev.azuresynapse.net"
        analyzer = PipelinesAnalyzer(fake_cfg, collector=collector)
        result = analyzer.run()

    # All five enumerations should come from the fake collector.
    assert {p.name for p in result.pipelines} == {"LoadSales", "BuildAggregates"}
    assert {a.name for a in result.activities} == {
        "CopyToWarehouse",
        "RunStored",
        "ExecuteChild",
    }
    assert {ls.name for ls in result.linked_services} == {
        "ls_azuresql_dwh",
        "ls_blob_landing",
    }
    assert {ds.name for ds in result.datasets} == {"ds_sales_src", "ds_sales_dst"}
    assert {t.name for t in result.triggers} == {"tr_daily_2am"}
    assert {ir.name for ir in result.integration_runtimes} == {
        "AutoResolveIntegrationRuntime"
    }

    # depends_on chain is preserved.
    run_stored = next(a for a in result.activities if a.name == "RunStored")
    assert run_stored.depends_on == ["CopyToWarehouse"]

    # Run-history step is skipped because we disabled the env flag above.
    assert result.run_history is None

    # Schedule mapper still runs (source-agnostic) and consumed our trigger.
    assert len(result.schedule_mappings) == 1
    assert result.schedule_mappings[0].trigger_name == "tr_daily_2am"


def test_analyzer_default_collector_is_synapse(
    fake_cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no collector is passed, the analyzer falls back to a
    Synapse collector wrapping the existing ``ArtifactsApiClient`` —
    preserving Phase 1 behavior for legacy callers."""
    monkeypatch.setenv("SMA_PIPELINES_RUN_HISTORY", "0")
    with patch(
        "usma.modules.pipelines.analyzer.ArtifactsApiClient"
    ) as mock_api_cls:
        mock_api_cls.return_value.endpoint = "https://ws-unused.dev.azuresynapse.net"
        # Make the patched client yield empty iterators so .run() finishes
        # without touching Azure.
        mock_api = mock_api_cls.return_value
        mock_api.iter_pipelines_with_activities.return_value = iter([])
        mock_api.list_linked_services.return_value = iter([])
        mock_api.list_datasets.return_value = iter([])
        mock_api.list_triggers.return_value = iter([])
        mock_api.list_integration_runtimes.return_value = iter([])

        analyzer = PipelinesAnalyzer(fake_cfg)
        result = analyzer.run()

    assert result.workspace_name == "ws-unused"
    assert result.artifacts_endpoint == "https://ws-unused.dev.azuresynapse.net"

def test_analyzer_uses_adf_run_history_for_adf_collector(
    fake_cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a real :class:`AdfCollector` is wired, the analyzer must
    construct an :class:`AdfRunHistoryClient` (not the Synapse
    :class:`RunHistoryClient`) and pass run rows through unchanged."""
    from usma.modules.pipelines.sources.adf_collector import (
        AdfCollector,
    )
    from usma.sources.adf.provider import AdfClientBundle

    bundle = AdfClientBundle(
        credential=MagicMock(),
        subscription_id="sub",
        resource_group="rg",
        factory_name="adf-fixture",
    )
    collector = AdfCollector(bundle)
    # Replace the lazy SDK client with a MagicMock returning empty iters
    # for the five enumeration calls in run().
    fake_mgmt = MagicMock()
    fake_mgmt.pipelines.list_by_factory.return_value = iter([])
    fake_mgmt.linked_services.list_by_factory.return_value = iter([])
    fake_mgmt.datasets.list_by_factory.return_value = iter([])
    fake_mgmt.triggers.list_by_factory.return_value = iter([])
    fake_mgmt.integration_runtimes.list_by_factory.return_value = iter([])
    collector._mgmt = fake_mgmt  # type: ignore[attr-defined]

    # The AdfRunHistoryClient should be constructed and called; patch
    # the class so we never touch real Azure.
    with patch(
        "usma.modules.pipelines.analyzer.AdfRunHistoryClient"
    ) as mock_adf_rh, patch(
        "usma.modules.pipelines.analyzer.RunHistoryClient"
    ) as mock_syn_rh, patch(
        "usma.modules.pipelines.analyzer.ArtifactsApiClient"
    ) as mock_api_cls:
        mock_api_cls.return_value.endpoint = "https://unused.dev.azuresynapse.net"
        mock_adf_rh.return_value.iter_pipeline_runs.return_value = iter([])
        mock_adf_rh.return_value.count_pipeline_runs.return_value = 0
        analyzer = PipelinesAnalyzer(fake_cfg, collector=collector)
        result = analyzer.run()

    # ADF client constructed exactly once; Synapse client untouched.
    mock_adf_rh.assert_called_once_with(bundle)
    mock_syn_rh.assert_not_called()
    # Endpoint comes from AdfCollector.endpoint (factory ARM ID).
    assert "Microsoft.DataFactory/factories/adf-fixture" in result.artifacts_endpoint
    # Run-history step ran (truthy object), even with no rows.
    assert result.run_history is not None
