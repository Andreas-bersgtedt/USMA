"""Tests for the `sma doctor` self-check."""
from __future__ import annotations

import os

from usma import doctor


def test_run_checks_offline_returns_results() -> None:
    """Offline run never raises and returns one result per check."""
    report = doctor.run_checks(offline=True)
    names = [r.name for r in report.results]
    # Core categories present
    assert "Python >= 3.12" in names
    assert "ODBC driver" in names
    assert "Azure CLI (az)" in names
    assert "Required env vars" in names
    assert "Output dir writable" in names
    # Live checks should be SKIP when --offline is set
    live = [r for r in report.results if r.name in (
        "AAD token (ARM)", "Synapse workspace reachable",
        "Spark Livy (Synapse Compute Operator)", "SQL access token",
        "Scope validation")]
    assert all(r.status == "skip" for r in live)


def test_run_checks_with_no_env_marks_required_vars_as_fail(monkeypatch) -> None:
    for k in (
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP", "SYNAPSE_WORKSPACE_NAME",
    ):
        monkeypatch.delenv(k, raising=False)
    # Pretend there is no .env on disk by running from tmp cwd
    monkeypatch.chdir(os.environ.get("TEMP") or "/tmp")
    report = doctor.run_checks(offline=True)
    env_check = next(r for r in report.results if r.name == "Required env vars")
    assert env_check.status == "fail"
    assert "Missing" in env_check.detail


def test_run_checks_packages_all_pass() -> None:
    """Every documented dependency must be importable in the dev environment."""
    report = doctor.run_checks(offline=True)
    pkg_results = [r for r in report.results if r.name.startswith("import ")]
    assert pkg_results, "expected per-package import checks"
    failed = [r for r in pkg_results if r.status == "fail"]
    assert not failed, f"missing packages: {[r.name for r in failed]}"


def test_render_report_smoke(capsys) -> None:
    """render_report should not raise and should emit something to the console."""
    from rich.console import Console

    report = doctor.run_checks(offline=True)
    console = Console(record=True, width=120)
    doctor.render_report(report, console)
    text = console.export_text()
    assert "sma doctor" in text
    assert "PASS" in text or "FAIL" in text or "SKIP" in text


def test_check_scopes_offline_skips() -> None:
    """``--offline`` should short-circuit scope validation to a single SKIP."""
    results = doctor._check_scopes(skip_live=True)
    assert len(results) == 1
    assert results[0].status == "skip"
    assert results[0].name == "Scope validation"


def test_check_scopes_no_aad_env_skips_aad_backed_scopes_per_scope(monkeypatch) -> None:
    """Missing AAD env vars should SKIP each AAD-backed scope individually
    (not short-circuit the whole check), so non-Azure scopes can still run."""
    from usma import sources as sources_pkg
    from usma.sources import SourceDescriptor, SourceType

    for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)

    scope = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="/subs/x/rg/y/ws/a",
        display_name="ws-a",
    )

    class _FakeCfg:
        scopes = (scope,)

    class _ShouldNotBeCalled:
        type = SourceType.SYNAPSE_WORKSPACE
        display_name = "Synapse"
        required_env: tuple[str, ...] = ()

        def discover(self, creds, subscription_id=None):  # pragma: no cover
            return []

        def validate(self, descriptor, creds):
            raise AssertionError("provider.validate must not run without AAD creds")

        def make_clients(self, descriptor, creds):  # pragma: no cover
            return None

    monkeypatch.setitem(sources_pkg.SOURCE_REGISTRY, SourceType.SYNAPSE_WORKSPACE, _ShouldNotBeCalled())
    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())

    results = doctor._check_scopes(skip_live=False)
    assert len(results) == 1
    assert results[0].status == "skip"
    assert "AAD env vars not set" in results[0].detail


def test_check_scopes_non_azure_scope_bypasses_aad_env_gate(monkeypatch) -> None:
    """BigQuery and other non-Azure scopes must validate even with no AAD env;
    the provider receives ``None`` creds and resolves via ADC itself."""
    from usma import sources as sources_pkg
    from usma.sources import (
        ConfigCheck,
        SourceDescriptor,
        SourceType,
    )

    for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)

    scope = SourceDescriptor(
        type=SourceType.BIGQUERY,
        id="my-gcp-project",
        display_name="my-gcp-project",
    )

    class _FakeCfg:
        scopes = (scope,)

    creds_received: list[object] = []

    class _BigQueryFake:
        type = SourceType.BIGQUERY
        display_name = "BigQuery"
        required_env: tuple[str, ...] = ()

        def discover(self, creds, subscription_id=None):  # pragma: no cover
            return []

        def validate(self, descriptor, creds):
            creds_received.append(creds)
            return [ConfigCheck(name="adc", ok=True, detail="adc-resolved")]

        def make_clients(self, descriptor, creds):  # pragma: no cover
            return None

    monkeypatch.setitem(sources_pkg.SOURCE_REGISTRY, SourceType.BIGQUERY, _BigQueryFake())
    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())

    results = doctor._check_scopes(skip_live=False)
    assert len(results) == 1
    assert results[0].status == "pass"
    assert results[0].name == "Scope: my-gcp-project (bigquery)"
    # Non-Azure provider must receive None (it resolves credentials itself).
    assert creds_received == [None]


def test_check_scopes_validates_each_configured_scope(monkeypatch, tmp_path) -> None:
    """When env + config + provider all line up, one CheckResult per scope."""
    from usma import sources as sources_pkg
    from usma.sources import (
        ConfigCheck,
        SourceDescriptor,
        SourceType,
    )

    # Populate the AAD env vars (the rest of load_config doesn't run because
    # we monkeypatch load_config below).
    monkeypatch.setenv("AZURE_TENANT_ID", "tid")
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")

    scope_a = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="/subs/x/rg/y/ws/a",
        display_name="ws-a",
    )
    scope_b = SourceDescriptor(
        type=SourceType.ADF,
        id="/subs/x/rg/y/factories/b",
        display_name="adf-b",
    )

    class _FakeCfg:
        scopes = (scope_a, scope_b)

    calls: list[tuple[SourceType, str]] = []

    class _PassProvider:
        type = SourceType.SYNAPSE_WORKSPACE
        display_name = "Synapse"
        required_env: tuple[str, ...] = ()

        def discover(self, creds, subscription_id=None):  # pragma: no cover
            return []

        def validate(self, descriptor, creds):
            calls.append((descriptor.type, descriptor.display_name))
            return [
                ConfigCheck(name="aad", ok=True, detail="ok"),
                ConfigCheck(name="reachable", ok=True),
            ]

        def make_clients(self, descriptor, creds):  # pragma: no cover
            return None

    class _FailProvider(_PassProvider):
        type = SourceType.ADF
        display_name = "ADF"

        def validate(self, descriptor, creds):
            calls.append((descriptor.type, descriptor.display_name))
            return [
                ConfigCheck(name="aad", ok=True, detail="ok"),
                ConfigCheck(name="dataplane", ok=False, detail="403"),
            ]

    monkeypatch.setitem(sources_pkg.SOURCE_REGISTRY, SourceType.SYNAPSE_WORKSPACE, _PassProvider())
    monkeypatch.setitem(sources_pkg.SOURCE_REGISTRY, SourceType.ADF, _FailProvider())
    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())

    results = doctor._check_scopes(skip_live=False)

    assert len(results) == 2
    assert calls == [(SourceType.SYNAPSE_WORKSPACE, "ws-a"), (SourceType.ADF, "adf-b")]
    by_name = {r.name: r for r in results}
    assert by_name["Scope: ws-a (synapse_workspace)"].status == "pass"
    assert "2 check(s) passed" in by_name["Scope: ws-a (synapse_workspace)"].detail
    assert by_name["Scope: adf-b (adf)"].status == "fail"
    assert "dataplane" in by_name["Scope: adf-b (adf)"].detail
    assert "403" in by_name["Scope: adf-b (adf)"].detail


def test_check_scopes_handles_provider_exception(monkeypatch) -> None:
    """A provider blowing up should produce a FAIL, not propagate."""
    from usma import sources as sources_pkg
    from usma.sources import SourceDescriptor, SourceType

    monkeypatch.setenv("AZURE_TENANT_ID", "tid")
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")

    scope = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="/subs/x/rg/y/ws/a",
        display_name="ws-a",
    )

    class _FakeCfg:
        scopes = (scope,)

    class _BoomProvider:
        type = SourceType.SYNAPSE_WORKSPACE
        display_name = "Synapse"
        required_env: tuple[str, ...] = ()

        def discover(self, creds, subscription_id=None):  # pragma: no cover
            return []

        def validate(self, descriptor, creds):
            raise RuntimeError("network exploded")

        def make_clients(self, descriptor, creds):  # pragma: no cover
            return None

    monkeypatch.setitem(sources_pkg.SOURCE_REGISTRY, SourceType.SYNAPSE_WORKSPACE, _BoomProvider())
    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())

    results = doctor._check_scopes(skip_live=False)
    assert len(results) == 1
    assert results[0].status == "fail"
    assert "network exploded" in results[0].detail


def test_check_scopes_load_config_failure_skips(monkeypatch) -> None:
    """If load_config raises (e.g. missing env), scope validation skips."""
    monkeypatch.setenv("AZURE_TENANT_ID", "tid")
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")

    def _raise(env_file=None):
        raise RuntimeError("Missing required environment variables: FOO")

    monkeypatch.setattr("usma.config.load_config", _raise)

    results = doctor._check_scopes(skip_live=False)
    assert len(results) == 1
    assert results[0].status == "skip"
    assert "config not loadable" in results[0].detail


# ---------------------------------------------------------------------------
# Phase 4.7 — Databricks per-platform routing
# ---------------------------------------------------------------------------


def test_check_scopes_aws_databricks_bypasses_aad_gate_and_uses_pat(
    monkeypatch,
) -> None:
    """AWS Databricks scopes must validate even with no AAD env vars; the
    provider's ``validate`` receives a synthetic ``Credentials`` whose
    ``extras`` carries ``DATABRICKS_TOKEN`` / OAuth client creds."""
    from usma.sources import (
        ConfigCheck,
        Credentials,
        SourceDescriptor,
        SourceType,
    )
    import usma.sources.databricks as dbx_pkg

    for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-fake")
    monkeypatch.setenv("DATABRICKS_HOST", "acme.cloud.databricks.com")

    scope = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="acme.cloud.databricks.com",
        display_name="acme",
        subscription_id=None,
        resource_group=None,
        extras={"platform": "aws", "workspace_url": "acme.cloud.databricks.com"},
    )

    class _FakeCfg:
        scopes = (scope,)

    creds_received: list[Credentials | None] = []

    class _FakeAwsProvider:
        type = SourceType.DATABRICKS
        display_name = "Databricks on AWS"

        def discover(self, creds, subscription_id=None):  # pragma: no cover
            return []

        def validate(self, descriptor, creds):
            creds_received.append(creds)
            return [ConfigCheck(name="rest", ok=True, detail="me")]

        def make_clients(self, descriptor, creds):  # pragma: no cover
            return None

    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())
    monkeypatch.setattr(dbx_pkg, "provider_for_descriptor", lambda d: _FakeAwsProvider())

    results = doctor._check_scopes(skip_live=False)

    assert len(results) == 1
    assert results[0].status == "pass"
    # Synthetic creds were built from env extras.
    assert len(creds_received) == 1
    creds = creds_received[0]
    assert creds is not None
    assert creds.extras.get("DATABRICKS_TOKEN") == "dapi-fake"
    assert creds.extras.get("DATABRICKS_HOST") == "acme.cloud.databricks.com"


def test_check_scopes_azure_databricks_still_skips_without_aad_env(
    monkeypatch,
) -> None:
    """Default-platform (Azure) Databricks scopes remain AAD-gated."""
    from usma.sources import SourceDescriptor, SourceType

    for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)

    # No ``extras['platform']`` -> defaults to azure via databricks_platform().
    scope = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="/subs/x/rg/y/Microsoft.Databricks/workspaces/z",
        display_name="z",
    )

    class _FakeCfg:
        scopes = (scope,)

    monkeypatch.setattr("usma.config.load_config", lambda env_file=None: _FakeCfg())

    results = doctor._check_scopes(skip_live=False)
    assert len(results) == 1
    assert results[0].status == "skip"
    assert "AAD env vars not set" in results[0].detail
