"""BigQueryProvider — Phase-5 Slice 5-A.

Discovers Google Cloud projects visible to the caller and produces SDK
clients for ``google-cloud-bigquery`` (datasets, tables, jobs),
``google-cloud-logging`` (audit-log slot-hour mining), and
``google-cloud-resource-manager`` (project enumeration).

Auth model — Application Default Credentials only
-------------------------------------------------
Credentials resolve through Google's ADC chain via ``google.auth.default()``:

  1. ``GOOGLE_APPLICATION_CREDENTIALS`` env var → service-account JSON key
  2. ``gcloud auth application-default login`` user credentials
  3. Workload Identity Federation config (the keyless / AAD-federated path)
  4. GCE / GKE / Cloud Run / Cloud Functions metadata server

USMA does not implement OAuth interactive flow, API keys, or end-user
refresh-token JSON. The Azure-shaped :class:`Credentials` object is
**not used** — ``BigQueryProvider`` accepts ``Credentials | None`` and
ignores ``tenant_id``/``client_id``/``client_secret`` entirely.

Optional escape hatch
~~~~~~~~~~~~~~~~~~~~~
If the caller cannot set ``GOOGLE_APPLICATION_CREDENTIALS`` ambiently
(e.g. multi-tenant SaaS), they may pass the service-account JSON path
via ``Credentials(extras={"gcp_service_account_json": "/path/to/sa.json"})``.
The provider sets ``GOOGLE_APPLICATION_CREDENTIALS`` for the duration
of the call and restores the prior value afterwards.

Descriptors
~~~~~~~~~~~
For BigQuery a *scope* is a single GCP project. ``SourceDescriptor.id``
is the project id (e.g. ``"my-gcp-project"``); ``display_name`` mirrors
it; ``subscription_id``/``resource_group``/``location`` are unset (those
are Azure concepts). ``extras`` carries:

* ``project_number`` — the numeric GCP project number
* ``parent`` — ``"organizations/123"`` / ``"folders/456"`` / ``None``
* ``gcp_service_account_json`` — only when set on the credential
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


@dataclass(frozen=True)
class BigQueryClientBundle:
    """SDK clients + identity needed by every BigQuery analyzer module.

    Mirrors :class:`DatabricksClientBundle` so callers can swap bundles
    based on ``descriptor.type`` without conditional branches on
    which-fields-exist.

    ``credentials`` is the ADC-resolved ``google.auth.credentials.Credentials``
    object (or ``None`` if the modules should let each SDK resolve ADC
    independently — useful when the caller wants per-call refresh).
    ``project_id`` is the GCP project id every module will need.
    ``sa_key_path`` is populated only when the escape-hatch was used, so
    modules can re-export it to subprocesses if needed.
    """

    credentials: Any  # google.auth.credentials.Credentials | None
    project_id: str
    project_number: str | None = None
    location: str | None = None
    sa_key_path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class BigQueryProvider(BaseSourceProvider):
    """Discovers GCP projects visible to ADC and validates BigQuery access."""

    type = SourceType.BIGQUERY
    display_name = "Google BigQuery"
    # No required env vars — ADC has multiple fallbacks. Documented in
    # docs/user-guide/22-bigquery.md and surfaced by the doctor command.
    required_env: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials | None = None,
        subscription_id: str | None = None,  # noqa: ARG002 — GCP has no subs
    ) -> list[SourceDescriptor]:
        """Enumerate every GCP project visible to ADC.

        Uses Cloud Resource Manager v3 ``search_projects(query="state:ACTIVE")``
        which returns projects the caller has ``resourcemanager.projects.get``
        permission on. Errors (missing ADC, 403 on Resource Manager) propagate
        as exceptions — :class:`web.config_io.discover_bigquery_projects`
        wraps them into :class:`ConfigCheck` rows for the SPA.
        """
        with _maybe_inject_sa_key(creds):
            credentials = _resolve_adc()
            try:
                from google.cloud import resourcemanager_v3
            except ImportError as exc:
                raise ImportError(
                    "The BigQuery source requires the optional '[bigquery]' "
                    "extra. Install it with: pip install -e \".[bigquery]\" "
                    "(adds google-cloud-bigquery + google-cloud-resource-manager "
                    "+ google-cloud-logging + google-auth)."
                ) from exc

            client = resourcemanager_v3.ProjectsClient(credentials=credentials)
            descriptors: list[SourceDescriptor] = []
            request = resourcemanager_v3.SearchProjectsRequest(query="state:ACTIVE")
            for project in client.search_projects(request=request):
                descriptors.append(_project_to_descriptor(project, creds))
            return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None = None,
    ) -> list[ConfigCheck]:
        """Run live BigQuery + Cloud Logging probes for a single project.

        Symmetric with :meth:`DatabricksProvider.validate` so all
        providers produce the same shape of :class:`ConfigCheck` rows
        for the SPA.
        """
        checks: list[ConfigCheck] = []
        project_id = descriptor.id
        with _maybe_inject_sa_key(creds):
            try:
                credentials = _resolve_adc(quota_project_id=project_id)
                checks.append(ConfigCheck(
                    name="GCP ADC resolution",
                    ok=True,
                    detail=_describe_credentials(credentials),
                    category="Control plane",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="GCP ADC resolution",
                    ok=False,
                    detail=str(exc),
                    category="Control plane",
                ))
                return checks  # downstream probes would just re-fail the same way

            # --- BigQuery API (project-level metadata) ----------------
            try:
                from google.cloud import bigquery as bq
            except ImportError as exc:
                checks.append(ConfigCheck(
                    name="BigQuery API",
                    ok=False,
                    detail=(
                        f"google-cloud-bigquery not importable: {exc}. "
                        "Install the [bigquery] extra."
                    ),
                    category="Data plane",
                ))
                return checks
            try:
                bq_client = bq.Client(project=project_id, credentials=credentials)
                sa_email = bq_client.get_service_account_email()
                checks.append(ConfigCheck(
                    name="BigQuery API",
                    ok=True,
                    detail=f"service account: {sa_email}",
                    category="Data plane",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="BigQuery API",
                    ok=False,
                    detail=str(exc),
                    category="Data plane",
                ))

            # --- Cloud Logging (audit-log slot-hour mining) -----------
            try:
                from google.cloud import logging_v2
            except ImportError as exc:
                checks.append(ConfigCheck(
                    name="Cloud Logging (audit logs)",
                    ok=False,
                    detail=(
                        f"google-cloud-logging not importable: {exc}. "
                        "Install the [bigquery] extra."
                    ),
                    category="Data plane",
                ))
                return checks
            try:
                log_client = logging_v2.Client(project=project_id, credentials=credentials)
                # No-op probe: ask for the first entry matching a cheap
                # filter. We don't iterate beyond one item; this proves
                # the SP has logging.logEntries.list on the project.
                iterator = log_client.list_entries(
                    filter_='logName="projects/{0}/logs/cloudaudit.googleapis.com%2Fdata_access"'.format(project_id),
                    page_size=1,
                    max_results=1,
                )
                # Force evaluation; iterator is lazy.
                next(iter(iterator), None)
                checks.append(ConfigCheck(
                    name="Cloud Logging (audit logs)",
                    ok=True,
                    detail="logging.logEntries.list reachable",
                    category="Data plane",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="Cloud Logging (audit logs)",
                    ok=False,
                    detail=str(exc),
                    category="Data plane",
                ))

        return checks

    # ------------------------------------------------------------------
    # Client bundle
    # ------------------------------------------------------------------
    def make_clients(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None = None,
    ) -> BigQueryClientBundle:
        """Construct the ADC credential + identity bundle BigQuery modules need.

        Individual modules build their own SDK clients
        (``bigquery.Client``, ``logging_v2.Client``, ...) on demand from
        the bundle. Keeps ``make_clients`` cheap and import-light.
        """
        if not descriptor.id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing id (GCP project id)",
            )
        sa_key_path = creds.extras.get("gcp_service_account_json") if creds else None
        with _maybe_inject_sa_key(creds):
            credentials = _resolve_adc(quota_project_id=descriptor.id)
        return BigQueryClientBundle(
            credentials=credentials,
            project_id=descriptor.id,
            project_number=descriptor.extras.get("project_number"),
            location=descriptor.location,
            sa_key_path=sa_key_path,
            extras=dict(descriptor.extras),
        )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _resolve_adc(quota_project_id: str | None = None) -> Any:
    """Resolve Application Default Credentials via ``google.auth.default()``.

    Returns the ``google.auth.credentials.Credentials`` object only;
    the discovered project id is intentionally ignored because USMA
    pins the project via the :class:`SourceDescriptor`.

    When ``quota_project_id`` is provided, the credentials are
    re-bound via ``with_quota_project`` so the per-request
    ``x-goog-user-project`` header is set. Without it, user-mode ADC
    (gcloud / 5-I browser sign-in) raises Google's
    "_CLOUD_SDK_CREDENTIALS_WARNING" and downstream APIs can return
    400/403 depending on the account's quota-project rules.
    """
    try:
        import google.auth
    except ImportError as exc:
        raise ImportError(
            "The BigQuery source requires the optional '[bigquery]' extra. "
            "Install it with: pip install -e \".[bigquery]\" "
            "(adds google-auth + google-cloud-bigquery)."
        ) from exc
    # ``google.auth.default()`` emits ``_CLOUD_SDK_CREDENTIALS_WARNING``
    # at the moment it discovers user-mode ADC, *before* we get a chance
    # to call ``with_quota_project``. The warning is harmless once we
    # rebind below, but pollutes server logs and confuses users — so
    # silence it for the duration of the discovery call only.
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.filterwarnings(
            "ignore",
            message="Your application has authenticated using end user credentials",
            category=UserWarning,
        )
        credentials, _ = google.auth.default()
    if quota_project_id and hasattr(credentials, "with_quota_project"):
        try:
            credentials = credentials.with_quota_project(quota_project_id)
        except Exception:  # noqa: BLE001
            # Some credential types (e.g. compute-engine metadata) don't
            # honor quota project rebind; falling back to the original
            # creds is safe and matches gcloud's behaviour.
            pass
    return credentials


@contextmanager
def _maybe_inject_sa_key(creds: Credentials | None) -> Iterator[None]:
    """Temporarily set ``GOOGLE_APPLICATION_CREDENTIALS`` from the escape hatch.

    No-op when ``creds`` is ``None`` or when ``extras['gcp_service_account_json']``
    is not set. Restores the prior env-var value on exit (whether or
    not it was set before).
    """
    sa_key = creds.extras.get("gcp_service_account_json") if creds else None
    if not sa_key:
        yield
        return
    sentinel = object()
    prior: Any = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", sentinel)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key
    try:
        yield
    finally:
        if prior is sentinel:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        else:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = prior


def _describe_credentials(credentials: Any) -> str:
    """Best-effort human label for a resolved ADC principal."""
    # ServiceAccountCredentials exposes ``service_account_email``;
    # UserCredentials exposes ``account``; Compute/ImpersonatedCredentials
    # expose neither reliably. Fall back to the class name.
    for attr in ("service_account_email", "account", "_account"):
        val = getattr(credentials, attr, None)
        if val:
            return str(val)
    return credentials.__class__.__name__


def _project_to_descriptor(
    project: Any,
    creds: Credentials | None,
) -> SourceDescriptor:
    """Adapt a Resource Manager ``Project`` to a :class:`SourceDescriptor`.

    Stashes the numeric project number + the parent (org/folder) under
    ``extras`` so the SPA can render a complete scope card without a
    re-fetch. When the caller used the SA-key escape hatch, we preserve
    the path under ``extras['gcp_service_account_json']`` so subsequent
    ``validate`` / ``make_clients`` calls find it again.
    """
    project_id = str(getattr(project, "project_id", "") or "")
    display_name = str(getattr(project, "display_name", "") or project_id)
    # Project names look like "projects/123456789012"; strip the prefix
    # so callers see only the numeric id.
    raw_name = str(getattr(project, "name", "") or "")
    project_number: str | None = None
    if raw_name.startswith("projects/"):
        project_number = raw_name[len("projects/"):]
    parent = str(getattr(project, "parent", "") or "") or None

    extras: dict[str, Any] = {}
    if project_number:
        extras["project_number"] = project_number
    if parent:
        extras["parent"] = parent
    if creds and creds.extras.get("gcp_service_account_json"):
        extras["gcp_service_account_json"] = creds.extras["gcp_service_account_json"]

    return SourceDescriptor(
        type=SourceType.BIGQUERY,
        id=project_id,
        display_name=display_name,
        extras=extras,
    )
