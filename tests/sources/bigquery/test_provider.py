"""Phase-5 Slice 5-A — :class:`BigQueryProvider` unit tests.

The GCP client libraries are heavy but installed via ``[dev]``; tests
still mock at the boundary so they run hermetically and fast.
"""
from __future__ import annotations

import os
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from usma.sources import (
    Credentials,
    SourceDescriptor,
    SourceType,
)
from usma.sources.bigquery.provider import (
    BigQueryClientBundle,
    BigQueryProvider,
    _maybe_inject_sa_key,
)


def _fake_project(project_id: str, number: str, *, parent: str | None = "organizations/123") -> Any:
    p = types.SimpleNamespace()
    p.project_id = project_id
    p.display_name = project_id
    p.name = f"projects/{number}"
    p.parent = parent
    return p


# ----------------------------------------------------------------------
# discover
# ----------------------------------------------------------------------
def test_discover_enumerates_active_projects_via_resource_manager(monkeypatch) -> None:
    fake_creds = MagicMock(name="adc_creds")
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (fake_creds, "ignored-project"),
    )
    fake_client = MagicMock()
    fake_client.search_projects.return_value = iter([
        _fake_project("proj-a", "111"),
        _fake_project("proj-b", "222", parent="folders/9"),
    ])
    with patch("google.cloud.resourcemanager_v3.ProjectsClient", return_value=fake_client):
        descriptors = BigQueryProvider().discover()

    assert [d.id for d in descriptors] == ["proj-a", "proj-b"]
    assert all(d.type == SourceType.BIGQUERY for d in descriptors)
    assert descriptors[0].extras["project_number"] == "111"
    assert descriptors[0].extras["parent"] == "organizations/123"
    assert descriptors[1].extras["parent"] == "folders/9"
    # Discover must have asked for ACTIVE-only projects (cheaper, dodges
    # archived/deleted noise).
    request = fake_client.search_projects.call_args.kwargs["request"]
    assert "state:ACTIVE" in str(request.query)


def test_discover_credentials_none_works(monkeypatch) -> None:
    """ADC discovery does not require an Azure-shaped Credentials object."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (MagicMock(), "proj"),
    )
    fake_client = MagicMock()
    fake_client.search_projects.return_value = iter([])
    with patch("google.cloud.resourcemanager_v3.ProjectsClient", return_value=fake_client):
        result = BigQueryProvider().discover(creds=None)
    assert result == []


def test_discover_subscription_id_arg_is_ignored(monkeypatch) -> None:
    """The Azure ``subscription_id`` arg is accepted but never used."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (MagicMock(), "proj"),
    )
    fake_client = MagicMock()
    fake_client.search_projects.return_value = iter([_fake_project("x", "1")])
    with patch("google.cloud.resourcemanager_v3.ProjectsClient", return_value=fake_client):
        result = BigQueryProvider().discover(creds=None, subscription_id="azure-sub-id-ignored")
    assert [d.id for d in result] == ["x"]


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------
def _bq_descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.BIGQUERY,
        id="my-gcp-project",
        display_name="my-gcp-project",
        extras={"project_number": "987654321"},
    )


def test_validate_happy_path_emits_three_checks(monkeypatch) -> None:
    """ADC + BigQuery API + Cloud Logging all succeed."""
    fake_adc = MagicMock(service_account_email="sa@proj.iam.gserviceaccount.com")
    fake_adc.with_quota_project.return_value = fake_adc
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (fake_adc, "my-gcp-project"),
    )

    fake_bq_client = MagicMock()
    fake_bq_client.get_service_account_email.return_value = "bq-system-sa@gcp"
    fake_log_client = MagicMock()
    fake_log_client.list_entries.return_value = iter([])  # no entries — call still succeeded

    with patch("google.cloud.bigquery.Client", return_value=fake_bq_client), \
         patch("google.cloud.logging_v2.Client", return_value=fake_log_client):
        checks = BigQueryProvider().validate(_bq_descriptor(), creds=None)

    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    names = [c.name for c in checks]
    assert names == [
        "GCP ADC resolution",
        "BigQuery API",
        "Cloud Logging (audit logs)",
    ]
    # ADC check describes the principal.
    assert "sa@proj.iam" in checks[0].detail
    # BigQuery check surfaces the GCP-managed BQ service account.
    assert "bq-system-sa" in checks[1].detail
    # Cloud Logging probe asked about the right project.
    log_filter = fake_log_client.list_entries.call_args.kwargs["filter_"]
    assert "my-gcp-project" in log_filter


def test_validate_adc_failure_short_circuits(monkeypatch) -> None:
    """When ADC cannot resolve, only the ADC check is emitted (FAIL)."""
    monkeypatch.setattr(
        "google.auth.default",
        MagicMock(side_effect=RuntimeError("no credentials found")),
    )
    checks = BigQueryProvider().validate(_bq_descriptor(), creds=None)
    assert len(checks) == 1
    assert checks[0].name == "GCP ADC resolution"
    assert checks[0].ok is False
    assert "no credentials" in checks[0].detail


def test_validate_bigquery_api_failure_is_isolated(monkeypatch) -> None:
    """A BQ failure must not prevent the Cloud Logging probe from running."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (MagicMock(account="user@x"), "proj"),
    )
    fake_bq_client = MagicMock()
    fake_bq_client.get_service_account_email.side_effect = PermissionError("403 bq")
    fake_log_client = MagicMock()
    fake_log_client.list_entries.return_value = iter([])

    with patch("google.cloud.bigquery.Client", return_value=fake_bq_client), \
         patch("google.cloud.logging_v2.Client", return_value=fake_log_client):
        checks = BigQueryProvider().validate(_bq_descriptor(), creds=None)

    by_name = {c.name: c for c in checks}
    assert by_name["BigQuery API"].ok is False
    assert "403 bq" in by_name["BigQuery API"].detail
    assert by_name["Cloud Logging (audit logs)"].ok is True


def test_validate_logging_failure_is_isolated(monkeypatch) -> None:
    """A Cloud Logging failure must not flag BigQuery as broken."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (MagicMock(), "proj"),
    )
    fake_bq_client = MagicMock()
    fake_bq_client.get_service_account_email.return_value = "sa@bq"
    fake_log_client = MagicMock()
    fake_log_client.list_entries.side_effect = PermissionError("403 logging")

    with patch("google.cloud.bigquery.Client", return_value=fake_bq_client), \
         patch("google.cloud.logging_v2.Client", return_value=fake_log_client):
        checks = BigQueryProvider().validate(_bq_descriptor(), creds=None)

    by_name = {c.name: c for c in checks}
    assert by_name["BigQuery API"].ok is True
    assert by_name["Cloud Logging (audit logs)"].ok is False
    assert "403 logging" in by_name["Cloud Logging (audit logs)"].detail


def test_validate_passes_project_id_to_clients(monkeypatch) -> None:
    """Both BQ and Logging clients must be constructed with the project id
    from the descriptor, NOT whatever ADC happened to default to."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (MagicMock(), "wrong-default-project"),
    )
    fake_bq_class = MagicMock()
    fake_bq_class.return_value.get_service_account_email.return_value = "sa"
    fake_log_class = MagicMock()
    fake_log_class.return_value.list_entries.return_value = iter([])

    with patch("google.cloud.bigquery.Client", fake_bq_class), \
         patch("google.cloud.logging_v2.Client", fake_log_class):
        BigQueryProvider().validate(_bq_descriptor(), creds=None)

    assert fake_bq_class.call_args.kwargs["project"] == "my-gcp-project"
    assert fake_log_class.call_args.kwargs["project"] == "my-gcp-project"


# ----------------------------------------------------------------------
# make_clients
# ----------------------------------------------------------------------
def test_make_clients_returns_bundle(monkeypatch) -> None:
    fake_adc = MagicMock()
    fake_adc.with_quota_project.return_value = fake_adc
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (fake_adc, "x"),
    )
    bundle = BigQueryProvider().make_clients(_bq_descriptor(), creds=None)
    assert isinstance(bundle, BigQueryClientBundle)
    assert bundle.project_id == "my-gcp-project"
    assert bundle.project_number == "987654321"
    assert bundle.credentials is fake_adc
    assert bundle.sa_key_path is None


def test_make_clients_records_sa_key_path(monkeypatch, tmp_path) -> None:
    fake_adc = MagicMock()
    monkeypatch.setattr(
        "google.auth.default",
        lambda *a, **kw: (fake_adc, "x"),
    )
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    creds = Credentials(
        tenant_id="",
        client_id="",
        client_secret="",
        extras={"gcp_service_account_json": str(key)},
    )
    bundle = BigQueryProvider().make_clients(_bq_descriptor(), creds=creds)
    assert bundle.sa_key_path == str(key)


def test_make_clients_rejects_descriptor_without_id() -> None:
    bad = SourceDescriptor(type=SourceType.BIGQUERY, id="", display_name="?")
    with pytest.raises(ValueError, match="missing id"):
        BigQueryProvider().make_clients(bad)


# ----------------------------------------------------------------------
# SA-key escape hatch
# ----------------------------------------------------------------------
def test_maybe_inject_sa_key_sets_and_restores_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/prior/path")
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    creds = Credentials(
        tenant_id="",
        client_id="",
        client_secret="",
        extras={"gcp_service_account_json": str(key)},
    )
    with _maybe_inject_sa_key(creds):
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key)
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/prior/path"


def test_maybe_inject_sa_key_removes_when_no_prior(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    creds = Credentials(
        tenant_id="",
        client_id="",
        client_secret="",
        extras={"gcp_service_account_json": str(key)},
    )
    with _maybe_inject_sa_key(creds):
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


def test_maybe_inject_sa_key_noop_when_creds_none() -> None:
    prior = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    with _maybe_inject_sa_key(None):
        assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") == prior
