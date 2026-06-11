"""Single source of truth for what each analyzer module touches.

Used by:
- the ``sma access-report`` CLI command to emit a Markdown summary that
  reviewers (InfoSec, change-advisory boards) can sign off on, and
- the user-guide chapter ``docs/user-guide/17-access-and-security.md``
  which is hand-written but references the same module list and RBAC roles
  so the two stay in sync.

Every entry describes a *read-only* surface. The analyzer never writes
to Synapse, Storage, or Monitor; the only filesystem writes are inside
``--output-dir`` / ``--runs-dir`` on the host running the CLI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleAccess:
    module: str
    purpose: str
    reads: tuple[str, ...]
    azure_rbac: tuple[str, ...]
    synapse_rbac: tuple[str, ...]
    sql_grants: tuple[str, ...]
    notes: str = ""


MANIFEST: tuple[ModuleAccess, ...] = (
    ModuleAccess(
        module="dedicated_pools",
        purpose="Inventory dedicated SQL pools and the SQL surface (tables, "
                "indexes, procedures, functions, top queries, top consumed "
                "tables/views). Supports both Synapse-workspace pools "
                "(Microsoft.Synapse/workspaces/sqlPools) and the standalone "
                "'Dedicated SQL pool (formerly SQL DW)' topology "
                "(Microsoft.Sql/servers/databases with edition='DataWarehouse'); "
                "see ADR-0009.",
        reads=(
            "SynapseManagementClient.workspaces.get / sql_pools.list_by_workspace "
            "(workspace topology)",
            "SqlManagementClient.servers.get / databases.list_by_server "
            "(standalone topology — filtered to sku.tier='DataWarehouse')",
            "T-SQL: sys.objects, sys.sql_modules, sys.parameters, sys.tables, "
            "sys.indexes, sys.dm_pdw_nodes_db_partition_stats, "
            "sys.dm_pdw_exec_requests, sys.dm_pdw_exec_sessions, "
            "INFORMATION_SCHEMA.TABLES (read-only DMV / catalog scans; "
            "identical MPP engine on both topologies)",
        ),
        azure_rbac=("Reader (workspace or RG)",),
        synapse_rbac=(),
        sql_grants=(
            "CREATE USER [<sp>] FROM EXTERNAL PROVIDER (per pool)",
            "db_datareader role membership",
            "VIEW DATABASE STATE (DMV access)",
            "VIEW DEFINITION (so sys.objects exposes procedures and UDFs)",
        ),
        notes="DMV scans are tagged with OPTION (LABEL = 'sma:<module>') so the "
              "analyzer's own activity is auditable in dm_pdw_exec_requests. "
              "The top-consumed-tables collector also writes a per-pool "
              "on-disk cache under output/.cache/dedicated_pools_workload/ "
              "so DMV roll-off does not gut the ranking between runs.",
    ),
    ModuleAccess(
        module="serverless_pools",
        purpose="Inventory the built-in serverless SQL endpoint, its databases "
                "and external tables, and (optionally) 7-day usage / cost "
                "from query history.",
        reads=(
            "T-SQL: sys.databases, sys.external_tables, sys.external_data_sources, "
            "sys.dm_exec_requests_history (data_processed)",
        ),
        azure_rbac=("Reader (workspace or RG)",),
        synapse_rbac=(),
        sql_grants=(
            "CREATE LOGIN [<sp>] FROM EXTERNAL PROVIDER (master)",
            "CREATE USER [<sp>] FROM EXTERNAL PROVIDER (per database, or master)",
            "VIEW SERVER STATE (needed for non-caller rows in "
            "dm_exec_requests_history; without it usage falls back to "
            "caller-only history and reports zero TB)",
        ),
    ),
    ModuleAccess(
        module="spark_pools",
        purpose="Inventory Spark pools and (optionally) collect Livy job "
                "history for Fabric CU-hour projection.",
        reads=(
            "SynapseManagementClient.big_data_pools.list_by_workspace",
            "Livy: SparkClient.spark_batch / spark_session list (paged, "
            "configurable window via SMA_SPARK_RUN_DAYS)",
        ),
        azure_rbac=("Reader (workspace or RG)",),
        synapse_rbac=(
            "Synapse Artifact User (workspace) — for notebooks / SJDs",
            "Synapse Compute Operator (pool or workspace) — required for Livy "
            "history; grants Microsoft.Synapse/workspaces/bigDataPools/"
            "useCompute/action",
        ),
        sql_grants=(),
        notes="Livy history is opt-out via SMA_SPARK_RUN_HISTORY=0. The "
              "analyzer never *starts* a Spark session — useCompute is needed "
              "only to *list* prior job runs.",
    ),
    ModuleAccess(
        module="pipelines",
        purpose="Inventory pipelines, datasets, linked services, notebooks, "
                "Spark Job Definitions, triggers, and (optionally) rolling "
                "7/14/28/90-day pipeline run history with per-activity data-"
                "movement metrics.",
        reads=(
            "ArtifactsClient pipeline/dataset/linkedService/notebook/SJD/trigger lists",
            "MonitorManagementClient pipeline & activity-run history (windowed; "
            "SMA_PIPELINES_RUN_DAYS, SMA_PIPELINES_RUN_LIMIT)",
        ),
        azure_rbac=("Reader (workspace or RG)",),
        synapse_rbac=("Synapse Artifact User (workspace)",),
        sql_grants=(),
    ),
    ModuleAccess(
        module="monitoring",
        purpose="Pull DWU capacity metrics for dedicated pools and Spark "
                "vCore-hour metrics for Spark pools.",
        reads=(
            "MonitorManagementClient.metrics.list on the workspace and pool resource IDs",
        ),
        azure_rbac=("Reader + Monitoring Reader (subscription or RG)",),
        synapse_rbac=(),
        sql_grants=(),
    ),
    ModuleAccess(
        module="storage",
        purpose="Inventory dedicated-pool table storage and the ADLS Gen2 / "
                "blob accounts linked to the workspace. Capacity only — never "
                "lists file contents.",
        reads=(
            "T-SQL (per pool): sys.dm_pdw_nodes_db_partition_stats joined "
            "through sys.pdw_table_mappings to sys.tables",
            "StorageManagementClient.storage_accounts.list / get_properties",
            "MonitorManagementClient.metrics (UsedCapacity)",
        ),
        azure_rbac=(
            "Reader (workspace or RG)",
            "Reader on each linked storage account (or subscription/RG-wide "
            "Reader for simplicity)",
            "Monitoring Reader (for UsedCapacity metric)",
        ),
        synapse_rbac=(),
        sql_grants=("Same as dedicated_pools (db_datareader + VIEW DATABASE STATE).",),
        notes="By default scoped to accounts referenced by Synapse linked "
              "services. Set SMA_STORAGE_INCLUDE_ALL=1 to fall back to the "
              "legacy subscription-wide scan.",
    ),
    ModuleAccess(
        module="fabric_mapping",
        purpose="Pure post-processing — consumes the JSON outputs of the other "
                "modules and produces readiness / recommendations / runbook / "
                "capacity projection. No Azure calls.",
        reads=("Reads only the analyzer's own JSON output files.",),
        azure_rbac=(),
        synapse_rbac=(),
        sql_grants=(),
    ),
    ModuleAccess(
        module="governance",
        purpose="Audit workspace RBAC, managed private endpoints, customer-"
                "managed keys, and Purview lineage configuration.",
        reads=(
            "AuthorizationManagementClient.role_assignments.list_for_scope / "
            "role_definitions.get",
            "SynapseManagementClient managed_private_endpoints / keys",
        ),
        azure_rbac=(
            "Reader (workspace or RG)",
            "Microsoft.Authorization/roleAssignments/read at the scopes being "
            "audited (Reader includes this for the workspace; subscription-"
            "level audit needs subscription-scoped Reader)",
        ),
        synapse_rbac=(),
        sql_grants=(),
    ),
    ModuleAccess(
        module="security",
        purpose="Audit firewall rules, AAD-only / TLS / public-network settings, "
                "TDE per pool, AAD admins, and a linked-service credential "
                "inventory (classifies as inline vs. Key Vault — never reads "
                "the secret material itself).",
        reads=(
            "SynapseManagementClient.ip_firewall_rules / workspace_aad_admins / "
            "sql_pool_transparent_data_encryptions",
            "ArtifactsClient.linked_service.get_linked_services_by_workspace",
        ),
        azure_rbac=("Reader (workspace or RG)",),
        synapse_rbac=("Synapse Artifact User (workspace) — for linked services",),
        sql_grants=(),
        notes="Credentials are classified by shape (Key Vault reference vs. "
              "inline ``{type: SecureString}``); the literal secret value is "
              "never read or persisted.",
    ),
    ModuleAccess(
        module="cost",
        purpose="Pull Microsoft Cost Management actuals (month-over-month, by "
                "resource kind) for a side-by-side Synapse vs. Fabric TCO view.",
        reads=("CostManagementClient.query.usage on the subscription or RG scope.",),
        azure_rbac=("Cost Management Reader (subscription or RG)",),
        synapse_rbac=(),
        sql_grants=(),
        notes="Opt-in. Requires the [cost] pip extra. Disable live calls with "
              "SMA_COST_DISABLE_LIVE=1.",
    ),
    ModuleAccess(
        module="fabric_validation",
        purpose="Post-migration runner: connects to a *target* Fabric warehouse "
                "and diffs object counts / row counts / collation / T-SQL "
                "surface against the prior dedicated_pools.json. Experimental.",
        reads=(
            "T-SQL (Fabric warehouse): INFORMATION_SCHEMA + sys.tables + "
            "sys.sql_modules (read-only)",
        ),
        azure_rbac=(),
        synapse_rbac=(),
        sql_grants=(
            "Fabric workspace 'Viewer' role (warehouse read) for the SP",
            "db_datareader on the target warehouse",
        ),
        notes="Opt-in via --include fabric_validation. Not run by default.",
    ),
)


# What the analyzer *writes* (host filesystem only — never back into Azure).
WRITE_SURFACE: tuple[str, ...] = (
    "./output/ (or --output-dir) — JSON, CSV, Markdown, HTML reports.",
    "./runs/ (or --runs-dir, control-plane only) — per-run folders.",
    "./.env (only via the control plane Configuration page; CLI never writes here).",
    "stderr / stdout — Rich-formatted or JSONL logs.",
)


# What output may contain that reviewers should treat as sensitive.
OUTPUT_SENSITIVITY: tuple[str, ...] = (
    "Workspace metadata: tenant id, subscription id, resource group, "
    "workspace name, pool names, region.",
    "Schema metadata: database / schema / table / view / procedure / function "
    "names, column names and data types, index definitions.",
    "Top-query previews: up to ~4 KB of SQL text per top query "
    "(dedicated_pools top_queries / serverless top_queries).",
    "Login / principal names from sys.dm_pdw_exec_sessions (no passwords).",
    "Linked-service definitions with credential *shape* (KV reference vs. "
    "inline SecureString) — literal secrets are never persisted.",
    "Cost Management actuals in USD-or-tenant-currency per resource.",
)


# What does NOT leave the host running the analyzer.
NON_EGRESS: tuple[str, ...] = (
    "No telemetry or analytics calls of any kind.",
    "No outbound network traffic except to documented Azure endpoints "
    "(management.azure.com, <workspace>.dev.azuresynapse.net, "
    "<workspace>.sql.azuresynapse.net, <workspace>-ondemand.sql.azuresynapse.net, "
    "<server>.database.windows.net (standalone Dedicated SQL pool topology), "
    "<storage>.dfs/blob.core.windows.net, management.azure.com Cost Management).",
    "The web control plane is loopback-only by default; --i-know-this-is-not-auth "
    "is required to bind a non-loopback host.",
)


def render_markdown(version: str) -> str:
    """Render the manifest as a stand-alone Markdown document."""
    lines: list[str] = []
    lines.append("# Unified Solution Migration Analyzer — Access & Security Report")
    lines.append("")
    lines.append(f"_Generated by `sma access-report` for analyzer version **{version}**._")
    lines.append("")
    lines.append("This is the canonical list of every Azure / Synapse surface the "
                 "analyzer touches, the RBAC required to make those calls succeed, "
                 "and what ends up in the on-disk output. All access is read-only.")
    lines.append("")
    lines.append("## Per-module access matrix")
    lines.append("")
    lines.append("| Module | Azure RBAC | Synapse RBAC | SQL grants |")
    lines.append("|---|---|---|---|")
    for m in MANIFEST:
        azr = "<br>".join(m.azure_rbac) if m.azure_rbac else "—"
        syr = "<br>".join(m.synapse_rbac) if m.synapse_rbac else "—"
        sql = "<br>".join(m.sql_grants) if m.sql_grants else "—"
        lines.append(f"| `{m.module}` | {azr} | {syr} | {sql} |")
    lines.append("")
    lines.append("## Per-module data sources")
    lines.append("")
    for m in MANIFEST:
        lines.append(f"### `{m.module}`")
        lines.append("")
        lines.append(m.purpose)
        lines.append("")
        lines.append("**Reads:**")
        lines.append("")
        for r in m.reads:
            lines.append(f"- {r}")
        if m.notes:
            lines.append("")
            lines.append(f"_Note: {m.notes}_")
        lines.append("")
    lines.append("## What gets written")
    lines.append("")
    for w in WRITE_SURFACE:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("## What ends up in output (treat as sensitive)")
    lines.append("")
    for s in OUTPUT_SENSITIVITY:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## What does NOT leave the host")
    lines.append("")
    for n in NON_EGRESS:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)
