"""Self-test / pre-flight check used by the `sma doctor` CLI command.

Each check is a small callable returning a `CheckResult`. The runner prints a
table and exits non-zero if any required check failed.
"""
from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from rich.console import Console
from rich.table import Table

Status = Literal["pass", "warn", "fail", "skip"]

_REQUIRED_ENV = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
    "SYNAPSE_RESOURCE_GROUP",
    "SYNAPSE_WORKSPACE_NAME",
)

_PACKAGES = (
    "azure.identity",
    "azure.mgmt.synapse",
    "azure.mgmt.resource",
    "azure.mgmt.monitor",
    "azure.mgmt.storage",
    "azure.synapse.artifacts",
    "azure.synapse.spark",
    "pyodbc",
    "pydantic",
    "click",
    "rich",
)


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    required: bool = True


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "fail" and r.required]

    @property
    def ok(self) -> bool:
        return not self.failed


# --------------------------------------------------------------------------- #
# Individual checks. Each returns a CheckResult and never raises.
# --------------------------------------------------------------------------- #

def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    detail = f"{platform.python_version()} on {platform.platform()}"
    if (major, minor) < (3, 12):
        return CheckResult("Python >= 3.12", "fail", detail)
    return CheckResult("Python >= 3.12", "pass", detail)


def _check_packages() -> list[CheckResult]:
    out: list[CheckResult] = []
    for mod in _PACKAGES:
        try:
            importlib.import_module(mod)
            out.append(CheckResult(f"import {mod}", "pass"))
        except ImportError as exc:
            out.append(CheckResult(f"import {mod}", "fail", str(exc)))
    return out


def _check_odbc_driver() -> CheckResult:
    from .platform import default_odbc_driver, odbc_install_hint

    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        return CheckResult("ODBC driver", "fail", f"pyodbc not importable: {exc}")
    drivers = [d for d in pyodbc.drivers() if "ODBC Driver" in d and "SQL Server" in d]
    if not drivers:
        return CheckResult(
            "ODBC driver",
            "fail",
            "No 'ODBC Driver xx for SQL Server' found.\n" + odbc_install_hint(),
        )
    configured = os.getenv("SQL_ODBC_DRIVER") or default_odbc_driver()
    if not any(d.lower() == configured.lower() for d in drivers):
        return CheckResult(
            "ODBC driver",
            "warn",
            f"Configured SQL_ODBC_DRIVER={configured!r} not installed; runtime will "
            f"fall back to the highest installed driver. Installed: "
            f"{', '.join(sorted(drivers))}. Update SQL_ODBC_DRIVER in .env to silence "
            "this warning.",
            required=False,
        )
    return CheckResult("ODBC driver", "pass", ", ".join(sorted(drivers)))


def _check_azure_cli() -> CheckResult:
    """Azure CLI is needed for the QUICKSTART §0.3 service-principal flow.

    It is *not* required at runtime by any analyzer (we use ClientSecretCredential),
    so we report this as a non-required WARN if missing.
    """
    import shutil
    import subprocess

    az = shutil.which("az")
    if az is None:
        return CheckResult(
            "Azure CLI (az)",
            "warn",
            "Not found on PATH. Needed only for QUICKSTART §0.3 (creating the service "
            "principal). See the Portal alternative if you cannot install it.",
            required=False,
        )
    try:
        proc = subprocess.run(
            [az, "version", "--output", "tsv", "--query", "\"azure-cli\""],
            capture_output=True, text=True, timeout=15, check=False,
        )
        version = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else "unknown"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            "Azure CLI (az)",
            "warn",
            f"Found at {az} but `az version` failed: {exc}",
            required=False,
        )
    return CheckResult("Azure CLI (az)", "pass", f"{az} (azure-cli {version})")


def _check_env_file() -> CheckResult:
    p = Path(".env")
    if not p.exists():
        return CheckResult(
            ".env present",
            "warn",
            "No ./.env found — copy .env.example and populate it for live checks.",
            required=False,
        )
    return CheckResult(".env present", "pass", str(p.resolve()))


def _check_env_vars() -> CheckResult:
    # Load ./.env only — never walk up to a parent project .env (matches
    # _check_env_file's contract and keeps the test isolated from ambient state).
    try:
        from dotenv import load_dotenv  # type: ignore

        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
    except ImportError:
        pass
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        return CheckResult(
            "Required env vars",
            "fail",
            f"Missing: {', '.join(missing)}",
        )
    return CheckResult("Required env vars", "pass", f"{len(_REQUIRED_ENV)} variables set")


def _check_output_dir() -> CheckResult:
    out = Path(os.getenv("SMA_OUTPUT_DIR", "./output")).resolve()
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".sma_doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("Output dir writable", "fail", f"{out}: {exc}")
    return CheckResult("Output dir writable", "pass", str(out))


def _check_aad_token(skip_live: bool) -> CheckResult:
    if skip_live:
        return CheckResult("AAD token (ARM)", "skip", "live checks disabled (--offline)", required=False)
    if any(not os.getenv(k) for k in _REQUIRED_ENV):
        return CheckResult("AAD token (ARM)", "skip", "env vars not set", required=False)
    try:
        from azure.identity import ClientSecretCredential

        cred = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        token = cred.get_token("https://management.azure.com/.default")
        return CheckResult("AAD token (ARM)", "pass", f"expires_on={token.expires_on}")
    except Exception as exc:  # noqa: BLE001 - surfaced to user as 'fail'
        return CheckResult("AAD token (ARM)", "fail", str(exc))


def _check_workspace_reachable(skip_live: bool) -> CheckResult:
    if skip_live:
        return CheckResult("Synapse workspace reachable", "skip", "live checks disabled (--offline)", required=False)
    if any(not os.getenv(k) for k in _REQUIRED_ENV):
        return CheckResult("Synapse workspace reachable", "skip", "env vars not set", required=False)
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.synapse import SynapseManagementClient

        cred = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        client = SynapseManagementClient(cred, os.environ["AZURE_SUBSCRIPTION_ID"])
        ws = client.workspaces.get(
            resource_group_name=os.environ["SYNAPSE_RESOURCE_GROUP"],
            workspace_name=os.environ["SYNAPSE_WORKSPACE_NAME"],
        )
        return CheckResult(
            "Synapse workspace reachable",
            "pass",
            f"{ws.name} ({ws.location})",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Synapse workspace reachable", "fail", str(exc))


def _check_spark_livy(skip_live: bool) -> CheckResult:
    """Probe Spark Livy on the first available pool — requires the Synapse RBAC
    action ``Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`` (granted
    by **Synapse Compute Operator** or higher). Returns ``warn`` if the workspace
    has no Spark pools (then `analyze-spark-pools` Livy history is N/A)."""
    name = "Spark Livy (Synapse Compute Operator)"
    if skip_live:
        return CheckResult(name, "skip", "live checks disabled (--offline)", required=False)
    if any(not os.getenv(k) for k in _REQUIRED_ENV):
        return CheckResult(name, "skip", "env vars not set", required=False)
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.synapse import SynapseManagementClient
        from azure.synapse.spark import SparkClient

        cred = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        ws_name = os.environ["SYNAPSE_WORKSPACE_NAME"]
        rg_name = os.environ["SYNAPSE_RESOURCE_GROUP"]
        mgmt = SynapseManagementClient(cred, os.environ["AZURE_SUBSCRIPTION_ID"])
        pool_names = [p.name for p in mgmt.big_data_pools.list_by_workspace(rg_name, ws_name)]
        if not pool_names:
            return CheckResult(
                name, "warn",
                "no Spark pools in workspace — Livy history is N/A",
                required=False,
            )
        probe = pool_names[0]
        spark = SparkClient(
            credential=cred,
            endpoint=f"https://{ws_name}.dev.azuresynapse.net",
            spark_pool_name=probe,
            livy_api_version="2019-11-01-preview",
        )
        try:
            spark.spark_batch.get_spark_batch_jobs(from_parameter=0, size=1, detailed=False)
        finally:
            try:
                spark.close()
            except Exception:  # noqa: BLE001
                pass
        return CheckResult(
            name, "pass",
            f"useCompute granted on '{probe}' ({len(pool_names)} pool(s))",
        )
    except Exception as exc:  # noqa: BLE001
        # 403 here typically means the SP lacks
        # `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`,
        # i.e. the **Synapse Compute Operator** role on the pool.
        return CheckResult(name, "fail", str(exc), required=False)


def _check_sql_token(skip_live: bool) -> CheckResult:
    if skip_live:
        return CheckResult("SQL access token", "skip", "live checks disabled (--offline)", required=False)
    if any(not os.getenv(k) for k in _REQUIRED_ENV):
        return CheckResult("SQL access token", "skip", "env vars not set", required=False)
    try:
        from azure.identity import ClientSecretCredential

        cred = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        cred.get_token("https://database.windows.net/.default")
        return CheckResult("SQL access token", "pass", "issued for database.windows.net")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("SQL access token", "fail", str(exc))


def _check_scopes(skip_live: bool) -> list[CheckResult]:
    """Per-scope validation via ``SourceProvider.validate()``.

    Loads the current ``AppConfig`` and asks each registered provider to
    validate its scope. One ``CheckResult`` is emitted per scope; the
    per-scope detail aggregates the provider's individual ``ConfigCheck``
    entries. AAD credentials are constructed lazily and only passed to
    AAD-backed providers (Synapse / ADF / Databricks); non-Azure
    providers (e.g. BigQuery via ADC) receive ``None`` and resolve
    credentials themselves. The check is skipped wholesale when
    ``--offline`` or when ``load_config`` cannot build an ``AppConfig``;
    individual AAD-backed scopes skip when the AAD env vars are missing.
    """
    if skip_live:
        return [CheckResult("Scope validation", "skip", "live checks disabled (--offline)", required=False)]
    try:
        from .config import load_config
        from .sources import Credentials, SourceType, get_provider
    except ImportError as exc:
        return [CheckResult("Scope validation", "skip", f"import error: {exc}", required=False)]
    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("Scope validation", "skip", f"config not loadable: {exc}", required=False)]
    if not cfg.scopes:
        return [CheckResult("Scope validation", "skip", "no scopes configured", required=False)]
    # Phase 4.7: ``DATABRICKS`` scopes are AAD-backed only on Azure;
    # AWS Databricks uses PAT / OAuth M2M and runs without an AAD env
    # gate. See ADR-0005.
    def _is_aad_backed(scope) -> bool:
        if scope.type is SourceType.DATABRICKS:
            from .sources.databricks import databricks_platform
            return databricks_platform(scope) == "azure"
        return scope.type in {
            SourceType.SYNAPSE_WORKSPACE,
            SourceType.SYNAPSE_DEDICATED_SQL,
            SourceType.ADF,
        }

    aad_vars = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
    aad_env_ok = all(os.environ.get(k) for k in aad_vars)
    aad_creds: Credentials | None = None
    if aad_env_ok:
        aad_creds = Credentials(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )

    def _aws_databricks_creds() -> Credentials | None:
        # Build a synthetic ``Credentials`` whose ``extras`` carries the
        # PAT / OAuth M2M secrets the AWS provider's ``validate`` reads.
        extras: dict[str, str] = {}
        for k in (
            "DATABRICKS_TOKEN",
            "DATABRICKS_CLIENT_ID",
            "DATABRICKS_CLIENT_SECRET",
            "DATABRICKS_HOST",
        ):
            v = os.environ.get(k)
            if v:
                extras[k] = v
        if not extras:
            return None
        return Credentials(
            tenant_id="", client_id="", client_secret="", extras=extras,
        )

    out: list[CheckResult] = []
    for scope in cfg.scopes:
        name = f"Scope: {scope.display_name} ({scope.type.value})"
        if _is_aad_backed(scope) and not aad_env_ok:
            out.append(CheckResult(name, "skip", "AAD env vars not set", required=False))
            continue
        try:
            provider = get_provider(scope.type)
        except KeyError as exc:
            out.append(CheckResult(name, "fail", f"no provider registered: {exc}"))
            continue
        # Per-platform credential routing:
        #   * AAD-backed scopes  -> Azure service-principal creds
        #   * AWS Databricks     -> synthetic Credentials w/ PAT/OAuth in extras
        #   * other (BigQuery)   -> None (ADC / SDK default chain)
        if _is_aad_backed(scope):
            creds = aad_creds
        elif scope.type is SourceType.DATABRICKS:
            creds = _aws_databricks_creds()
        else:
            creds = None
        # Phase 4.7: AWS Databricks scopes need a per-platform provider
        # dispatch — the registry default for ``DATABRICKS`` returns the
        # Azure provider, which would attempt an ARM probe on a missing
        # subscription_id. Route via ``provider_for_descriptor`` instead.
        if scope.type is SourceType.DATABRICKS:
            from .sources.databricks import provider_for_descriptor
            provider = provider_for_descriptor(scope)
        try:
            checks = provider.validate(scope, creds)
        except NotImplementedError as exc:
            out.append(CheckResult(name, "skip", f"provider stub: {exc}", required=False))
            continue
        except Exception as exc:  # noqa: BLE001
            out.append(CheckResult(name, "fail", str(exc)))
            continue
        if not checks:
            out.append(CheckResult(name, "skip", "provider returned no checks", required=False))
            continue
        failed = [c for c in checks if not c.ok]
        if failed:
            detail = "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in failed)
            out.append(CheckResult(name, "fail", detail))
        else:
            out.append(CheckResult(name, "pass", f"{len(checks)} check(s) passed"))
    return out


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run_checks(*, offline: bool = False) -> DoctorReport:
    report = DoctorReport()
    checks: list[Callable[[], CheckResult | list[CheckResult]]] = [
        _check_python,
        _check_packages,
        _check_odbc_driver,
        _check_azure_cli,
        _check_env_file,
        _check_env_vars,
        _check_output_dir,
        lambda: _check_aad_token(offline),
        lambda: _check_workspace_reachable(offline),
        lambda: _check_spark_livy(offline),
        lambda: _check_sql_token(offline),
        lambda: _check_scopes(offline),
    ]
    for fn in checks:
        result = fn()
        if isinstance(result, list):
            report.results.extend(result)
        else:
            report.results.append(result)
    return report


_STATUS_STYLE = {
    "pass": "[green]PASS[/green]",
    "warn": "[yellow]WARN[/yellow]",
    "fail": "[red]FAIL[/red]",
    "skip": "[dim]SKIP[/dim]",
}


def render_report(report: DoctorReport, console: Console) -> None:
    table = Table(title="sma doctor")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")
    for r in report.results:
        table.add_row(r.name, _STATUS_STYLE[r.status], r.detail)
    console.print(table)
    if report.ok:
        console.print("[bold green]All required checks passed.[/bold green]")
    else:
        console.print(
            f"[bold red]{len(report.failed)} required check(s) failed.[/bold red] "
            "Review the table above and fix before running an analyzer."
        )
