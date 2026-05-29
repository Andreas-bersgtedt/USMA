import { useEffect, useState } from "react";
import HelpLink from "../components/HelpLink";
import {
  apiDiscoverBigqueryProjects,
  apiDiscoverDatabricksWorkspaces,
  apiDiscoverFactories,
  apiDiscoverSnowflakeDatabases,
  apiDiscoverWorkspaces,
  apiExportRunsArchive,
  apiGcpAuthLogin,
  apiGcpAuthStatus,
  apiGetConfig,
  apiGetEffortCard,
  apiImportRunsArchive,
  apiPutConfig,
  apiPutEffortCard,
  apiResetEffortCard,
  apiSnowflakeAuthLogin,
  apiSnowflakeAuthStatus,
  apiValidateConfig,
  type AppConfig,
  type EffortCardResponse,
  type GcpAuthStatus,
  type RunsImportMode,
  type RunsImportResult,
  type SnowflakeAuthStatus,
  type SourceType,
  type ValidateResponse,
  type WorkspaceSummary,
} from "../api/loader";

/** Phase 4.7 — buckets the Azure SP / Synapse "resource group + workspace"
 *  fields into the per-cloud groups the Configuration page now renders.
 *  "azure" covers Synapse / ADF / Azure-Databricks; "gcp" only BigQuery;
 *  "aws" only AWS-Databricks. The `Dedicated pool` field stays Synapse-only. */
type CloudBucket = "azure" | "gcp" | "aws" | "snowflake";
function cloudBucket(
  src: SourceType,
  platform: "azure" | "aws" | null,
): CloudBucket {
  if (src === "bigquery") return "gcp";
  if (src === "snowflake") return "snowflake";
  if (src === "databricks" && platform === "aws") return "aws";
  return "azure";
}

function workspaceKey(rg: string | null, name: string | null): string {
  if (!rg || !name) return "";
  return `${rg}/${name}`;
}

/** Pick the right discovery endpoint for the configured source type. */
function discoverFor(src: SourceType): () => Promise<ValidateResponse> {
  if (src === "adf") return apiDiscoverFactories;
  if (src === "databricks") return apiDiscoverDatabricksWorkspaces;
  if (src === "bigquery") return apiDiscoverBigqueryProjects;
  if (src === "snowflake") return apiDiscoverSnowflakeDatabases;
  return apiDiscoverWorkspaces;
}

/** Noun for the scope ("workspace" / "factory" / "project" / "account"). */
function scopeNoun(src: SourceType): string {
  if (src === "adf") return "factory";
  if (src === "bigquery") return "project";
  if (src === "snowflake") return "account";
  return "workspace";
}
function scopeNounPlural(src: SourceType, count: number): string {
  if (src === "adf") return count === 1 ? "factory" : "factories";
  if (src === "bigquery") return count === 1 ? "project" : "projects";
  if (src === "snowflake") return count === 1 ? "account" : "accounts";
  return count === 1 ? "workspace" : "workspaces";
}

/** Full label for the section header / discover button. */
function scopeLabel(src: SourceType): string {
  if (src === "adf") return "Data factory";
  if (src === "databricks") return "Databricks workspace";
  if (src === "bigquery") return "GCP project";
  if (src === "snowflake") return "Snowflake account";
  return "Synapse workspace";
}
function discoverVerbObject(src: SourceType): string {
  if (src === "adf") return "factories";
  if (src === "databricks") return "Databricks workspaces";
  if (src === "bigquery") return "GCP projects";
  if (src === "snowflake") return "Snowflake account";
  return "workspaces";
}

export default function Configuration(): JSX.Element {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string>("");
  // Phase 4.7 — Databricks-on-AWS uses a single Databricks service
  // principal (DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET) for
  // both account-level workspace discovery and per-workspace REST auth.
  // Same write-only contract as ``secret`` above: blank means “leave
  // the existing env var alone”, non-blank is written through verbatim.
  const [dbxClientSecret, setDbxClientSecret] = useState<string>("");
  // Phase 7 Slice 7-E.1 — Snowflake OAuth secrets. Same write-only
  // contract as ``secret`` above: blank means “leave the existing env
  // var alone”, non-blank is written through verbatim.
  const [snowflakeOauthClientSecret, setSnowflakeOauthClientSecret] = useState<string>("");
  const [snowflakeOauthRefreshToken, setSnowflakeOauthRefreshToken] = useState<string>("");
  const [snowflakeOauthToken, setSnowflakeOauthToken] = useState<string>("");
  const [validation, setValidation] = useState<ValidateResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  // Slice 5-I — browser-based GCP sign-in. While ``gcpAuth.state ===
  // "pending"`` we poll the backend every 1500ms; on ``ready`` we kick
  // off project discovery automatically so the user lands on a
  // populated dropdown without an extra click.
  const [gcpAuth, setGcpAuth] = useState<GcpAuthStatus>({ state: "idle" });
  const [gcpAuthMsg, setGcpAuthMsg] = useState<string | null>(null);
  // Slice 7-E.2 — browser-based Snowflake OAuth sign-in. Same
  // pending/poll/ready/error state machine as the GCP flow above, but
  // the result is a long-lived refresh token persisted into ``.env``
  // rather than an ADC user-credentials file.
  const [snowflakeAuth, setSnowflakeAuth] = useState<SnowflakeAuthStatus>({ state: "idle" });
  const [snowflakeAuthMsg, setSnowflakeAuthMsg] = useState<string | null>(null);

  useEffect(() => {
    apiGetConfig().then(setCfg).catch((e: Error) => setError(e.message));
  }, []);

  // Auto-discover workspaces / factories once the four required
  // credentials are saved. The endpoint we call depends on the source
  // type toggle — switching the toggle resets the dropdown so this
  // effect re-runs against the new endpoint.
  useEffect(() => {
    if (!cfg) return;
    if (workspaces !== null) return;
    if (discovering) return;
    const bucket = cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform);
    const ready =
      bucket === "gcp"
        ? true
        : bucket === "snowflake"
          ? (
              !!cfg.azure.snowflake_account
              && !!cfg.azure.snowflake_user
              && !!cfg.azure.snowflake_warehouse
              && (
                cfg.azure.snowflake_oauth_token === "set"
                || (
                  !!cfg.azure.snowflake_oauth_client_id
                  && cfg.azure.snowflake_oauth_client_secret === "set"
                  && cfg.azure.snowflake_oauth_refresh_token === "set"
                )
              )
            )
          : bucket === "aws"
          ? (
              !!cfg.azure.databricks_host
              || (
                !!cfg.azure.databricks_account_id
                && !!cfg.azure.databricks_client_id
                && cfg.azure.databricks_client_secret === "set"
              )
            )
          : (
              !!cfg.azure.tenant_id
              && !!cfg.azure.client_id
              && !!cfg.azure.subscription_id
              && cfg.azure.client_secret === "set"
            );
    if (!ready) return;
    setDiscovering(true);
    setDiscoverError(null);
    const fetcher = discoverFor(cfg.azure.source_type);
    fetcher()
      .then((r) => setWorkspaces(r.workspaces ?? []))
      .catch((e: Error) => setDiscoverError(e.message))
      .finally(() => setDiscovering(false));
  }, [cfg, workspaces, discovering]);

  // Slice 5-I — poll the GCP auth status while a sign-in is pending.
  // The browser-based OAuth flow runs on a background thread; the SPA
  // only knows it's done by polling /api/auth/gcp/status. Once the
  // state flips to ``ready`` we clear the cached workspaces so the
  // discover effect above re-fires against the new ADC token.
  useEffect(() => {
    if (gcpAuth.state !== "pending") return;
    const timer = window.setInterval(async () => {
      try {
        const next = await apiGcpAuthStatus();
        setGcpAuth(next);
        if (next.state === "ready") {
          setGcpAuthMsg(
            next.email
              ? `Signed in as ${next.email}. Refreshing project list…`
              : "Signed in to Google. Refreshing project list…",
          );
          setWorkspaces(null);
        } else if (next.state === "error") {
          setGcpAuthMsg(next.error ?? "Sign-in failed.");
        }
      } catch (e) {
        setGcpAuthMsg(`Status poll failed: ${(e as Error).message}`);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [gcpAuth.state]);

  const onGcpSignIn = async () => {
    setGcpAuthMsg("Opening browser for Google sign-in…");
    setGcpAuth({ state: "pending" });
    try {
      const r = await apiGcpAuthLogin();
      setGcpAuth({ state: r.state });
      setGcpAuthMsg(r.message);
    } catch (e) {
      setGcpAuth({ state: "error" });
      setGcpAuthMsg(`Failed to start sign-in: ${(e as Error).message}`);
    }
  };

  // Slice 7-E.2 — poll the Snowflake auth status while a sign-in is
  // pending. On ``ready`` we reload the config so the refresh-token
  // pill flips to ``set`` and clear the cached workspaces so the
  // discover effect re-fires.
  useEffect(() => {
    if (snowflakeAuth.state !== "pending") return;
    const timer = window.setInterval(async () => {
      try {
        const next = await apiSnowflakeAuthStatus();
        setSnowflakeAuth(next);
        if (next.state === "ready") {
          setSnowflakeAuthMsg(
            `Refresh token persisted for ${next.account ?? "the configured account"}. Reloading configuration…`,
          );
          apiGetConfig().then(setCfg).catch(() => undefined);
          setWorkspaces(null);
        } else if (next.state === "error") {
          setSnowflakeAuthMsg(next.error ?? "Sign-in failed.");
        }
      } catch (e) {
        setSnowflakeAuthMsg(`Status poll failed: ${(e as Error).message}`);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [snowflakeAuth.state]);

  const onSnowflakeSignIn = async () => {
    setSnowflakeAuthMsg("Opening browser for Snowflake sign-in…");
    setSnowflakeAuth({ state: "pending" });
    try {
      const r = await apiSnowflakeAuthLogin();
      setSnowflakeAuth({ state: r.state });
      setSnowflakeAuthMsg(r.message);
    } catch (e) {
      setSnowflakeAuth({ state: "error" });
      setSnowflakeAuthMsg(`Failed to start sign-in: ${(e as Error).message}`);
    }
  };

  if (error) return <div className="empty">Error loading config: {error}</div>;
  if (!cfg) return <div className="empty">Loading…</div>;

  const update = (group: "azure" | "sql", key: string, value: string) => {
    setCfg({
      ...cfg,
      [group]: { ...cfg[group], [key]: value },
    });
  };

  const updateWorkspace = (name: string, resource_group: string) => {
    setCfg({
      ...cfg,
      azure: { ...cfg.azure, workspace_name: name, resource_group },
    });
  };

  const onSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      const body: Record<string, unknown> = {
        azure: { ...cfg.azure },
        sql: { ...cfg.sql },
      };
      if (secret) (body.azure as Record<string, unknown>).client_secret = secret;
      else delete (body.azure as Record<string, unknown>).client_secret;
      // Phase 4.7 — the single Databricks SP secret. Forward only when
      // the user typed a new value; the API returns the literal string
      // ``"set"`` / ``"unset"`` on reads, which the backend would
      // otherwise mistake for a real secret to persist.
      const az = body.azure as Record<string, unknown>;
      if (dbxClientSecret) az.databricks_client_secret = dbxClientSecret;
      else delete az.databricks_client_secret;
      // Phase 7 Slice 7-E.1 — Snowflake OAuth secrets (client secret,
      // refresh token, optional pre-minted access token). Same
      // forwarding contract as the secrets above.
      if (snowflakeOauthClientSecret) az.snowflake_oauth_client_secret = snowflakeOauthClientSecret;
      else delete az.snowflake_oauth_client_secret;
      if (snowflakeOauthRefreshToken) az.snowflake_oauth_refresh_token = snowflakeOauthRefreshToken;
      else delete az.snowflake_oauth_refresh_token;
      if (snowflakeOauthToken) az.snowflake_oauth_token = snowflakeOauthToken;
      else delete az.snowflake_oauth_token;
      const r = await apiPutConfig(body);
      setSaveMsg(`Saved to ${r.saved_to}` + (r.warnings.length ? ` (${r.warnings.join("; ")})` : ""));
      setSecret("");
      setDbxClientSecret("");
      setSnowflakeOauthClientSecret("");
      setSnowflakeOauthRefreshToken("");
      setSnowflakeOauthToken("");
      const fresh = await apiGetConfig();
      setCfg(fresh);
    } catch (e) {
      setSaveMsg(`Error: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const onValidate = async (live: boolean) => {
    setValidating(true);
    setValidation(null);
    try {
      const r = await apiValidateConfig({ live });
      setValidation(r);
      if (live && r.workspaces) setWorkspaces(r.workspaces);
    } catch (e) {
      setSaveMsg(`Validate failed: ${(e as Error).message}`);
    } finally {
      setValidating(false);
    }
  };

  const onDiscoverWorkspaces = async () => {
    if (!cfg) return;
    setDiscovering(true);
    setDiscoverError(null);
    try {
      const fetcher = discoverFor(cfg.azure.source_type);
      const r = await fetcher();
      setWorkspaces(r.workspaces ?? []);
      // Surface any discovery-time checks (e.g. AAD failure) inline.
      if (r.checks.length > 0) setValidation(r);
    } catch (e) {
      setDiscoverError((e as Error).message);
    } finally {
      setDiscovering(false);
    }
  };

  const onChangeSourceType = (next: SourceType) => {
    if (!cfg || cfg.azure.source_type === next) return;
    // Clear the dropdown + selected resource so the effect re-runs against
    // the right endpoint. We keep the rest of the form intact since the
    // four credentials apply to both source types.
    setCfg({
      ...cfg,
      azure: {
        ...cfg.azure,
        source_type: next,
        workspace_name: null,
        resource_group: null,
        gcp_project_id: null,
      },
    });
    setWorkspaces(null);
    setDiscoverError(null);
  };

  // Phase 4.7 — Databricks platform sub-radio (Azure | AWS). Empty string
  // clears the env var on save so the backend falls back to ``"azure"``.
  const onChangeDatabricksPlatform = (next: "azure" | "aws") => {
    if (!cfg) return;
    if ((cfg.azure.databricks_platform ?? "azure") === next) return;
    setCfg({
      ...cfg,
      azure: {
        ...cfg.azure,
        databricks_platform: next,
        // Switching cloud invalidates any previously discovered scope.
        workspace_name: null,
        resource_group: null,
      },
    });
    setWorkspaces(null);
    setDiscoverError(null);
  };

  return (
    <section className="page">
      <h1>Configuration <HelpLink slug="12-configuration" /></h1>
      <p className="muted">
        Edit the connection settings persisted to <code>{cfg.env_file}</code>.
        The client secret is never returned by the API — its current status is
        shown as <code>{cfg.azure.client_secret}</code> (<em>set</em> or <em>unset</em>).
      </p>

      <div className="card" style={{ marginTop: "1rem" }}>
        <div className="label">Source type</div>
        <div role="radiogroup" aria-label="Source type" style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button
            type="button"
            role="radio"
            aria-checked={cfg.azure.source_type === "synapse_workspace"}
            className={cfg.azure.source_type === "synapse_workspace" ? "pill ok" : "pill"}
            onClick={() => onChangeSourceType("synapse_workspace")}
            disabled={saving}
            title="Inventory a Synapse Analytics workspace (dedicated pools, Spark, pipelines, serverless)"
          >
            Synapse workspace
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={cfg.azure.source_type === "adf"}
            className={cfg.azure.source_type === "adf" ? "pill ok" : "pill"}
            onClick={() => onChangeSourceType("adf")}
            disabled={saving}
            title="Inventory a standalone Azure Data Factory (pipelines only)"
          >
            Data Factory
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={cfg.azure.source_type === "databricks"}
            className={cfg.azure.source_type === "databricks" ? "pill ok" : "pill"}
            onClick={() => onChangeSourceType("databricks")}
            disabled={saving}
            title="ALPHA — Databricks support is an alpha-quality preview. Inventories an Azure Databricks workspace (workflows, jobs, clusters); some analyzer rules and Fabric-mapping projections are still being calibrated. Please report issues."
          >
            Databricks <sup className="alpha-badge" aria-label="alpha preview">alpha</sup>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={cfg.azure.source_type === "bigquery"}
            className={cfg.azure.source_type === "bigquery" ? "pill ok" : "pill"}
            onClick={() => onChangeSourceType("bigquery")}
            disabled={saving}
            title="ALPHA — BigQuery support is an alpha-quality preview. Inventories a Google BigQuery project (datasets, tables, routines, scheduled queries, slot-hour jobs) via Application Default Credentials; some analyzer rules and Fabric-mapping projections are still being calibrated. Please report issues."
          >
            BigQuery <sup className="alpha-badge" aria-label="alpha preview">alpha</sup>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={cfg.azure.source_type === "snowflake"}
            className={cfg.azure.source_type === "snowflake" ? "pill ok" : "pill"}
            onClick={() => onChangeSourceType("snowflake")}
            disabled={saving}
            title="ALPHA — Snowflake support is an alpha-quality preview. Inventories a Snowflake account (warehouses, databases, schemas, tables, routines, tasks, streams, pipes, query history) via key-pair JWT; some analyzer rules and Fabric-mapping projections are still being calibrated. Please report issues."
          >
            Snowflake <sup className="alpha-badge" aria-label="alpha preview">alpha</sup>
          </button>
        </div>
        <p className="small muted" style={{ marginTop: 6 }}>
          {cfg.azure.source_type === "adf"
            ? "ADF mode: only the pipelines module runs. The workspace selector below lists ADF factories."
            : cfg.azure.source_type === "databricks"
              ? "Databricks mode: runs the databricks_workflows + cost + fabric_mapping modules. The selector below lists Databricks workspaces."
              : cfg.azure.source_type === "bigquery"
                ? "BigQuery mode: runs the bigquery_workloads + cost + fabric_mapping modules. Auth uses Application Default Credentials — set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON key or run 'gcloud auth application-default login'. Cost attribution reads the BigQuery billing-export tables — set SMA_GCP_BILLING_DATASET and SMA_GCP_BILLING_TABLE (or SMA_GCP_BILLING_ACCOUNT) to enable."
                : cfg.azure.source_type === "snowflake"
                  ? "Snowflake mode: runs the snowflake_workloads + fabric_mapping modules. Auth uses key-pair JWT — provide account, user, private key (PEM string or file path), optional passphrase, role, and warehouse below. The cloud platform (AWS / Azure / GCP) is inferred from CURRENT_REGION() at run time."
                  : "Synapse mode: all inventory modules run against the selected workspace."}
        </p>
        {cfg.azure.source_type === "databricks" && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--surface-border, #2a2f3a)" }}>
            <div className="label">Databricks cloud platform</div>
            <div
              role="radiogroup"
              aria-label="Databricks cloud platform"
              style={{ display: "flex", gap: 8, marginTop: 6 }}
            >
              <button
                type="button"
                role="radio"
                aria-checked={(cfg.azure.databricks_platform ?? "azure") === "azure"}
                className={(cfg.azure.databricks_platform ?? "azure") === "azure" ? "pill ok" : "pill"}
                onClick={() => onChangeDatabricksPlatform("azure")}
                disabled={saving}
                title="Azure Databricks workspace, AAD-federated. Discovery enumerates workspaces under the configured subscription."
              >
                Azure
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={cfg.azure.databricks_platform === "aws"}
                className={cfg.azure.databricks_platform === "aws" ? "pill ok" : "pill"}
                onClick={() => onChangeDatabricksPlatform("aws")}
                disabled={saving}
                title="ALPHA — Databricks on AWS support is an alpha-quality preview. Auth uses a single Databricks service principal (DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET) for both Account API discovery (DATABRICKS_ACCOUNT_ID) and per-workspace REST auth."
              >
                AWS <sup className="alpha-badge" aria-label="alpha preview">alpha</sup>
              </button>
            </div>
            {cfg.azure.databricks_platform === "aws" && (
              <p className="small muted" style={{ marginTop: 6 }}>
                Databricks-on-AWS does not use the AAD service principal — the
                Azure SP / Subscription fields below are hidden. Provide a
                Databricks service principal (client ID + secret) and an
                account ID, then click <em>Discover Databricks workspaces</em>{" "}
                to enumerate via the Databricks Account API. The same SP is
                also used for per-workspace REST auth. CLI users can drive
                multi-scope runs via <code>--scope databricks-aws:&lt;host&gt;</code>.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Per-cloud connection settings. The flat AZURE_* SP form only
          makes sense for Synapse / ADF / Azure-Databricks; AWS-Databricks
          gets its own block; BigQuery uses ADC and renders no form here. */}
      {cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform) === "azure" && (
        <div className="form-grid">
          <label>
            <span>Tenant ID</span>
            <input
              type="text"
              value={cfg.azure.tenant_id ?? ""}
              onChange={(e) => update("azure", "tenant_id", e.target.value)}
            />
          </label>
          <label>
            <span>Client ID</span>
            <input
              type="text"
              value={cfg.azure.client_id ?? ""}
              onChange={(e) => update("azure", "client_id", e.target.value)}
            />
          </label>
          <label>
            <span>Subscription ID</span>
            <input
              type="text"
              value={cfg.azure.subscription_id ?? ""}
              onChange={(e) => update("azure", "subscription_id", e.target.value)}
            />
          </label>
          {cfg.azure.source_type === "synapse_workspace" && (
            <label>
              <span>Dedicated pool (optional)</span>
              <input
                type="text"
                value={cfg.azure.dedicated_pool ?? ""}
                onChange={(e) => update("azure", "dedicated_pool", e.target.value)}
              />
            </label>
          )}
          <label>
            <span>ODBC driver</span>
            <input
              type="text"
              value={cfg.sql.odbc_driver}
              onChange={(e) => update("sql", "odbc_driver", e.target.value)}
            />
          </label>
          <label>
            <span>Client secret ({cfg.azure.client_secret})</span>
            <input
              type="password"
              placeholder={cfg.azure.client_secret === "set" ? "leave blank to keep" : "paste new value"}
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="off"
            />
          </label>
        </div>
      )}

      {cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform) === "aws" && (
        <div className="form-grid">
          <label style={{ gridColumn: "1 / -1" }}>
            <span>
              Workspace host
              <span className="small muted" style={{ marginLeft: 6 }}>
                e.g. <code>https://dbc-abc12345-6789.cloud.databricks.com</code> — optional when Account ID + SP are set
              </span>
            </span>
            <input
              type="text"
              value={cfg.azure.databricks_host ?? ""}
              placeholder="https://dbc-...cloud.databricks.com"
              onChange={(e) => update("azure", "databricks_host", e.target.value)}
            />
          </label>
          <label>
            <span>
              Account ID
              <span className="small muted" style={{ marginLeft: 6 }}>
                enables workspace discovery
              </span>
            </span>
            <input
              type="text"
              value={cfg.azure.databricks_account_id ?? ""}
              placeholder="Databricks account UUID"
              onChange={(e) => update("azure", "databricks_account_id", e.target.value)}
            />
          </label>
          <label>
            <span>Databricks SP client ID</span>
            <input
              type="text"
              value={cfg.azure.databricks_client_id ?? ""}
              placeholder="service principal client id"
              onChange={(e) => update("azure", "databricks_client_id", e.target.value)}
            />
          </label>
          <label>
            <span>Databricks SP client secret ({cfg.azure.databricks_client_secret})</span>
            <input
              type="password"
              placeholder={cfg.azure.databricks_client_secret === "set" ? "leave blank to keep" : "paste secret"}
              value={dbxClientSecret}
              onChange={(e) => setDbxClientSecret(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            <span>ODBC driver</span>
            <input
              type="text"
              value={cfg.sql.odbc_driver}
              onChange={(e) => update("sql", "odbc_driver", e.target.value)}
            />
          </label>
        </div>
      )}

      {cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform) === "gcp" && (
        <div className="form-grid">
          <p className="small muted" style={{ gridColumn: "1 / -1" }}>
            BigQuery authenticates via Application Default Credentials — the
            Azure service-principal fields are hidden in this mode. Sign in to
            Google below to write an ADC file, or set{" "}
            <code>GOOGLE_APPLICATION_CREDENTIALS</code> to a service-account JSON
            key on the host.
          </p>
          <label>
            <span>ODBC driver</span>
            <input
              type="text"
              value={cfg.sql.odbc_driver}
              onChange={(e) => update("sql", "odbc_driver", e.target.value)}
            />
          </label>
        </div>
      )}

      {cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform) === "snowflake" && (
        <div className="form-grid">
          <p className="small muted" style={{ gridColumn: "1 / -1" }}>
            Snowflake authenticates via a built-in OAuth integration —
            the Azure service-principal fields are hidden in this mode.
            Provide the account locator
            (<code>&lt;orgname&gt;-&lt;accountname&gt;</code>), the user
            the OAuth integration binds tokens to, the role to assume,
            the warehouse the analyzer uses for its own queries, and the
            OAuth client credentials. USMA exchanges the long-lived
            refresh token for short-lived access tokens at run time
            against <code>https://&lt;account&gt;.snowflakecomputing.com/oauth/token-request</code>.
            (Mint the refresh token once out-of-band via the
            authorisation-code flow against your custom security
            integration.) The optional pre-minted access token is a
            break-glass / CI escape hatch; it expires ~10 minutes after
            issuance.
          </p>
          <label>
            <span>Account</span>
            <input
              type="text"
              value={cfg.azure.snowflake_account ?? ""}
              placeholder="myorg-myacct"
              onChange={(e) => update("azure", "snowflake_account", e.target.value)}
            />
          </label>
          <label>
            <span>User</span>
            <input
              type="text"
              value={cfg.azure.snowflake_user ?? ""}
              placeholder="SVC_USMA"
              onChange={(e) => update("azure", "snowflake_user", e.target.value)}
            />
          </label>
          <label>
            <span>Role</span>
            <input
              type="text"
              value={cfg.azure.snowflake_role ?? ""}
              placeholder="USMA_RO"
              onChange={(e) => update("azure", "snowflake_role", e.target.value)}
            />
          </label>
          {(cfg.azure.snowflake_role ?? "").trim().toUpperCase() === "PUBLIC" && (
            <div
              style={{
                gridColumn: "1 / -1",
                padding: "8px 12px",
                background: "#fff3cd",
                border: "1px solid #ffeeba",
                borderRadius: 4,
                color: "#856404",
                fontSize: 13,
              }}
            >
              ⚠ <strong>PUBLIC role detected.</strong> Snowflake does not
              allow <code>IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE</code>{" "}
              (required for cost + query-history reads) to be granted to
              PUBLIC. Create a dedicated role per the bootstrap SQL in{" "}
              <code>docs/user-guide/23-snowflake.md</code> (e.g.{" "}
              <code>USMA_RO</code>), set it here, Save, then click{" "}
              <em>Sign in to Snowflake</em> again so the refresh token is
              re-minted for the new role.
            </div>
          )}
          {cfg.azure.snowflake_oauth_refresh_token === "set"
            && (cfg.azure.snowflake_role ?? "").trim().toUpperCase() !== "PUBLIC"
            && (cfg.azure.snowflake_role ?? "").trim() !== "" && (
            <div
              style={{
                gridColumn: "1 / -1",
                padding: "8px 12px",
                background: "#e7f3ff",
                border: "1px solid #b8daff",
                borderRadius: 4,
                color: "#004085",
                fontSize: 13,
              }}
            >
              ℹ If you just changed the role, click{" "}
              <em>Sign in to Snowflake</em> again — refresh tokens are
              bound to the role at consent time, so the existing token
              still authorises only the previous role.
            </div>
          )}
          <label>
            <span>Warehouse</span>
            <input
              type="text"
              value={cfg.azure.snowflake_warehouse ?? ""}
              placeholder="USMA_WH"
              onChange={(e) => update("azure", "snowflake_warehouse", e.target.value)}
            />
          </label>
          <label>
            <span>OAuth client id</span>
            <input
              type="text"
              value={cfg.azure.snowflake_oauth_client_id ?? ""}
              placeholder="custom integration CLIENT_ID"
              onChange={(e) => update("azure", "snowflake_oauth_client_id", e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            <span>OAuth client secret ({cfg.azure.snowflake_oauth_client_secret})</span>
            <input
              type="password"
              value={snowflakeOauthClientSecret}
              placeholder={
                cfg.azure.snowflake_oauth_client_secret === "set"
                  ? "leave blank to keep"
                  : "custom integration CLIENT_SECRET"
              }
              onChange={(e) => setSnowflakeOauthClientSecret(e.target.value)}
              autoComplete="off"
            />
          </label>
          <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            <button
              type="button"
              onClick={onSnowflakeSignIn}
              disabled={
                snowflakeAuth.state === "pending"
                || !cfg.azure.snowflake_account
                || !cfg.azure.snowflake_oauth_client_id
                || cfg.azure.snowflake_oauth_client_secret !== "set"
              }
              title="Open browser to sign into Snowflake and mint a long-lived refresh token"
            >
              {snowflakeAuth.state === "pending"
                ? "Waiting for browser…"
                : cfg.azure.snowflake_oauth_refresh_token === "set"
                  ? "Re-sign in to Snowflake"
                  : "Sign in to Snowflake"}
            </button>
            {snowflakeAuth.state === "ready" && snowflakeAuth.account && (
              <span className="small muted">
                Refresh token persisted for <code>{snowflakeAuth.account}</code>
              </span>
            )}
            {snowflakeAuthMsg && (
              <span
                className="small"
                style={{
                  color:
                    snowflakeAuth.state === "error"
                      ? "var(--err, #c33)"
                      : "var(--muted, #666)",
                }}
              >
                {snowflakeAuthMsg}
              </span>
            )}
          </div>
          <p className="small muted" style={{ gridColumn: "1 / -1", marginTop: 0 }}>
            Save the account + OAuth client id + OAuth client secret
            first, then click <em>Sign in to Snowflake</em> to mint the
            refresh token via a one-time browser consent. The token is
            written into your <code>.env</code> as
            <code> SNOWFLAKE_OAUTH_REFRESH_TOKEN</code> and reused
            indefinitely. Browser sign-in only works when USMA runs on
            your local machine. The integration's
            <code> OAUTH_REDIRECT_URI</code> must match the URI used
            here <strong>exactly</strong> (Snowflake does not allow
            wildcards). Default is <code>http://localhost:53682/</code>;
            override via <code>SNOWFLAKE_OAUTH_REDIRECT_URI</code> in
            <code> .env</code>.
          </p>
          <label style={{ gridColumn: "1 / -1" }}>
            <span>
              OAuth refresh token ({cfg.azure.snowflake_oauth_refresh_token})
              <span className="small muted" style={{ marginLeft: 6 }}>
                long-lived; populated automatically by the Sign in button (or paste manually)
              </span>
            </span>
            <textarea
              rows={3}
              value={snowflakeOauthRefreshToken}
              placeholder={
                cfg.azure.snowflake_oauth_refresh_token === "set"
                  ? "leave blank to keep"
                  : "ver:1-hint:..."
              }
              onChange={(e) => setSnowflakeOauthRefreshToken(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            <span>
              Pre-minted access token ({cfg.azure.snowflake_oauth_token})
              <span className="small muted" style={{ marginLeft: 6 }}>
                optional break-glass / CI escape hatch — expires ~10 minutes after issuance
              </span>
            </span>
            <textarea
              rows={2}
              value={snowflakeOauthToken}
              placeholder={
                cfg.azure.snowflake_oauth_token === "set"
                  ? "leave blank to keep"
                  : "leave blank for refresh-token flow"
              }
              onChange={(e) => setSnowflakeOauthToken(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label>
            <span>ODBC driver</span>
            <input
              type="text"
              value={cfg.sql.odbc_driver}
              onChange={(e) => update("sql", "odbc_driver", e.target.value)}
            />
          </label>
        </div>
      )}

      {(() => {
        const isBigQuery = cfg.azure.source_type === "bigquery";
        const isSnowflake = cfg.azure.source_type === "snowflake";
        // Databricks-on-AWS has no Azure resource group, so
        // ``workspaceKey("", name)`` collapses to "" and every option in
        // the dropdown would share that key — the <select> can't track
        // which one is selected. Key AWS rows by their workspace URL
        // (``sql_endpoint``) instead, which mirrors how the backend
        // identifies the workspace via ``DATABRICKS_HOST``.
        const isAwsDatabricks =
          cfg.azure.source_type === "databricks"
          && cfg.azure.databricks_platform === "aws";
        const awsCurrentHost = (cfg.azure.databricks_host ?? "").trim();
        const currentKey = isBigQuery
          ? (cfg.azure.gcp_project_id ?? "")
          : isAwsDatabricks
            ? awsCurrentHost
            : workspaceKey(cfg.azure.resource_group, cfg.azure.workspace_name);
        const list = workspaces ?? [];
        const awsKey = (w: WorkspaceSummary): string =>
          (w.sql_endpoint ?? "").trim() || w.name;
        const knownKeys = new Set(
          list.map((w) =>
            isBigQuery
              ? w.name
              : isAwsDatabricks
                ? awsKey(w)
                : workspaceKey(w.resource_group, w.name),
          ),
        );
        const currentInList = currentKey !== "" && knownKeys.has(currentKey);
        const bucket = cloudBucket(cfg.azure.source_type, cfg.azure.databricks_platform);
        // BigQuery uses ADC (no AAD SP). AWS Databricks needs either an
        // explicit workspace host or the OAuth account triple. Azure /
        // Synapse / ADF / Azure-Databricks share the legacy SP gate.
        const credsReady =
          bucket === "gcp"
            ? true
            : bucket === "snowflake"
              ? (
                  !!cfg.azure.snowflake_account
                  && !!cfg.azure.snowflake_user
                  && !!cfg.azure.snowflake_warehouse
                  && (
                    cfg.azure.snowflake_oauth_token === "set"
                    || (
                      !!cfg.azure.snowflake_oauth_client_id
                      && cfg.azure.snowflake_oauth_client_secret === "set"
                      && cfg.azure.snowflake_oauth_refresh_token === "set"
                    )
                  )
                )
              : bucket === "aws"
              ? (
                  !!cfg.azure.databricks_host
                  || (
                    !!cfg.azure.databricks_account_id
                    && !!cfg.azure.databricks_client_id
                    && cfg.azure.databricks_client_secret === "set"
                  )
                )
              : (
                  !!cfg.azure.tenant_id
                  && !!cfg.azure.client_id
                  && !!cfg.azure.subscription_id
                  && cfg.azure.client_secret === "set"
                );
        const credsReadyHint =
          bucket === "aws"
            ? "Fill in Workspace host (or Account ID + Databricks SP credentials), then Save"
            : bucket === "snowflake"
              ? "Fill in Account, User, Warehouse, and OAuth credentials (client id + secret + refresh token, or pre-minted access token), then Save"
              : "Fill in Tenant, Client, Secret, Subscription and Save first";
        return (
          <div className="card" style={{ marginTop: "1rem" }}>
            <div className="label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span>{scopeLabel(cfg.azure.source_type)}</span>
              <button
                type="button"
                onClick={onDiscoverWorkspaces}
                disabled={discovering || saving || !credsReady}
                title={
                  credsReady
                    ? `List ${discoverVerbObject(cfg.azure.source_type)} in the configured subscription`
                    : credsReadyHint
                }
              >
                {discovering
                  ? "Discovering…"
                  : workspaces
                    ? "Refresh list"
                    : `Discover ${discoverVerbObject(cfg.azure.source_type)}`}
              </button>
              {workspaces && (
                <span className="small muted">
                  {list.length}{" "}
                  {scopeNounPlural(cfg.azure.source_type, list.length)}{" "}
                  in subscription
                </span>
              )}
              {discoverError && (
                <span className="small" style={{ color: "var(--err, #c33)" }}>{discoverError}</span>
              )}
            </div>

            {!credsReady && (
              <p className="small muted" style={{ marginTop: 6 }}>
                Enter Tenant ID, Client ID, Client secret, and Subscription ID
                above and click <em>Save configuration</em>. The workspace
                dropdown will auto-populate from the subscription — no need to
                type a resource group or workspace name.
              </p>
            )}

            {credsReady && isBigQuery && (
              <div className="form-grid" style={{ marginTop: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    onClick={onGcpSignIn}
                    disabled={gcpAuth.state === "pending"}
                    title="Open browser to sign into Google and write ADC credentials (no gcloud CLI required)"
                  >
                    {gcpAuth.state === "pending"
                      ? "Waiting for browser…"
                      : gcpAuth.state === "ready"
                        ? "Re-sign in to Google"
                        : "Sign in to Google"}
                  </button>
                  {gcpAuth.state === "ready" && gcpAuth.email && (
                    <span className="small muted">
                      Signed in as <code>{gcpAuth.email}</code>
                    </span>
                  )}
                  {gcpAuthMsg && (
                    <span
                      className="small"
                      style={{
                        color:
                          gcpAuth.state === "error"
                            ? "var(--err, #c33)"
                            : "var(--muted, #666)",
                      }}
                    >
                      {gcpAuthMsg}
                    </span>
                  )}
                </div>
                <p className="small muted" style={{ marginTop: 0 }}>
                  Browser sign-in only works when SMA runs on your local
                  machine (the OAuth callback redirects to <code>localhost</code>).
                  For server deployments, use a service-account JSON key
                  or Workload Identity Federation instead.
                </p>
                <label>
                  <span>GCP project</span>
                  <select
                    value={currentInList ? currentKey : ""}
                    disabled={list.length === 0}
                    onChange={(e) => {
                      const v = e.target.value;
                      const ws = list.find((w) => w.name === v);
                      if (ws) {
                        setCfg({
                          ...cfg,
                          azure: {
                            ...cfg.azure,
                            gcp_project_id: ws.name,
                            // Mirror the project id into workspace_name
                            // so labels/scope chips keep rendering a name
                            // without a separate code path.
                            workspace_name: ws.name,
                            resource_group: null,
                          },
                        });
                      }
                    }}
                  >
                    <option value="" disabled>
                      {list.length === 0
                        ? discovering
                          ? "— discovering… —"
                          : "— click Discover GCP projects —"
                        : "— select a project —"}
                    </option>
                    {list.map((w) => (
                      <option key={w.name} value={w.name}>
                        {w.location ? `${w.location} — ${w.name}` : w.name}
                        {w.resource_group ? ` (project #${w.resource_group})` : ""}
                        {w.is_current ? " — current" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                {currentKey !== "" && !currentInList && list.length > 0 && (
                  <p className="small" style={{ color: "var(--err, #c33)", marginTop: 4 }}>
                    Saved project <code>{cfg.azure.gcp_project_id}</code> is
                    not visible via Application Default Credentials. Pick one
                    from the dropdown or fix the ADC environment.
                  </p>
                )}
              </div>
            )}

            {credsReady && isSnowflake && (
              <div className="form-grid" style={{ marginTop: 6 }}>
                <p className="small muted" style={{ gridColumn: "1 / -1", marginTop: 0 }}>
                  A Snowflake "scope" is the single account the key-pair
                  JWT authorises. Click <em>Discover Snowflake account</em>{" "}
                  above to probe the account and surface its cloud
                  platform + region below.
                </p>
                {list.length > 0 && (
                  <div style={{ gridColumn: "1 / -1" }}>
                    {list.map((w) => (
                      <p key={w.name} className="small" style={{ marginTop: 0 }}>
                        Account <code>{w.name}</code>
                        {w.location ? ` — ${w.location}` : ""}
                        {w.resource_group && !w.location ? ` (${w.resource_group})` : ""}
                        {w.is_current ? " — current" : ""}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {credsReady && !isBigQuery && !isSnowflake && (
              <div className="form-grid" style={{ marginTop: 6 }}>
                <label>
                  <span>{cfg.azure.source_type === "adf" ? "Factory" : "Workspace"}</span>
                  <select
                    value={currentInList ? currentKey : ""}
                    disabled={list.length === 0}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (isAwsDatabricks) {
                        const ws = list.find((w) => awsKey(w) === v);
                        if (ws) {
                          // Persist both the workspace URL (used by the
                          // backend as DATABRICKS_HOST at run-time) and
                          // the display name so the scope chip / status
                          // row renders something human-readable.
                          setCfg({
                            ...cfg,
                            azure: {
                              ...cfg.azure,
                              databricks_host: ws.sql_endpoint ?? "",
                              workspace_name: ws.name,
                              resource_group: null,
                            },
                          });
                        }
                        return;
                      }
                      const ws = list.find(
                        (w) => workspaceKey(w.resource_group, w.name) === v,
                      );
                      if (ws) updateWorkspace(ws.name, ws.resource_group);
                    }}
                  >
                    <option value="" disabled>
                      {list.length === 0
                        ? discovering
                          ? "— discovering… —"
                          : `— click Discover ${discoverVerbObject(cfg.azure.source_type)} —`
                        : `— select a ${scopeNoun(cfg.azure.source_type)} —`}
                    </option>
                    {list.map((w) => {
                      const key = isAwsDatabricks
                        ? awsKey(w)
                        : workspaceKey(w.resource_group, w.name);
                      const label = isAwsDatabricks
                        ? `${w.name}${w.sql_endpoint ? ` — ${w.sql_endpoint}` : ""}${w.is_current ? " — current" : ""}`
                        : `${w.name} (${w.resource_group}${w.location ? `, ${w.location}` : ""})${w.is_current ? " — current" : ""}`;
                      return (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      );
                    })}
                  </select>
                </label>
                {currentKey !== "" && !currentInList && list.length > 0 && (
                  <p className="small" style={{ color: "var(--err, #c33)", marginTop: 4 }}>
                    {isAwsDatabricks ? (
                      <>
                        Saved workspace host{" "}
                        <code>{cfg.azure.databricks_host}</code> is not visible
                        to the Databricks service principal on this account.
                        Pick one from the dropdown.
                      </>
                    ) : (
                      <>
                        Saved {scopeNoun(cfg.azure.source_type)}{" "}
                        <code>{cfg.azure.workspace_name}</code> (resource group{" "}
                        <code>{cfg.azure.resource_group}</code>) is not visible
                        to the service principal in this subscription. Pick one
                        from the dropdown.
                      </>
                    )}
                  </p>
                )}
                {currentKey !== "" && currentInList && !isAwsDatabricks && (
                  <p className="small muted" style={{ marginTop: 4 }}>
                    Resource group <code>{cfg.azure.resource_group}</code> set
                    automatically from the selected{" "}
                    {scopeNoun(cfg.azure.source_type)}.
                  </p>
                )}
                {currentKey !== "" && currentInList && isAwsDatabricks && (
                  <p className="small muted" style={{ marginTop: 4 }}>
                    Workspace host <code>{cfg.azure.databricks_host}</code> set
                    automatically — click <em>Save configuration</em> to persist.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })()}

      <div className="actions">
        <button onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save configuration"}
        </button>
        <button onClick={() => onValidate(false)} disabled={validating} title="Local checks only: env vars present, GUIDs well-formed, output directory writable">
          {validating ? "Validating…" : "Validate fields"}
        </button>
        <button onClick={() => onValidate(true)} disabled={validating} title="Field checks plus live Azure + Synapse connectivity using the saved service principal">
          {validating ? "Testing…" : "Validate live access"}
        </button>
        {saveMsg && <span className="muted">{saveMsg}</span>}
      </div>

      {validation && (
        <div className="card" style={{ marginTop: "1rem" }}>
          <div className="label">
            Validation —{" "}
            <span className={`pill ${validation.ok ? "ok" : "err"}`}>
              {validation.ok ? "All OK" : "Failed"}
            </span>
          </div>
          {(() => {
            // Group by category, preserving insertion order.
            const groups = new Map<string, typeof validation.checks>();
            for (const c of validation.checks) {
              const k = c.category || "Configuration";
              if (!groups.has(k)) groups.set(k, []);
              groups.get(k)!.push(c);
            }
            return Array.from(groups.entries()).map(([cat, items]) => (
              <div key={cat} style={{ marginTop: "0.5rem" }}>
                <div className="small muted" style={{ fontWeight: 600 }}>{cat}</div>
                <ul>
                  {items.map((c) => (
                    <li key={c.name}>
                      <span className={`pill ${c.ok ? "ok" : "err"}`}>{c.ok ? "OK" : "Fail"}</span>{" "}
                      <code>{c.name}</code>
                      {c.detail && <span className="muted"> — {c.detail}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            ));
          })()}

          {validation.workspaces && validation.workspaces.length > 0 && (
            <div className="small muted" style={{ marginTop: "0.75rem" }}>
              {validation.workspaces.length} accessible Synapse workspace
              {validation.workspaces.length === 1 ? "" : "s"} — pick one in the
              <em> Synapse workspace</em> selector above.
            </div>
          )}
        </div>
      )}

      <EffortCardEditor />
      <RunsBackupRestore />
    </section>
  );
}

/**
 * Collapsible **webform** editor for the configurable effort-rate card.
 *
 * Lives behind a `<details>` so the Configuration page stays clean for
 * the 99 % of users who never touch it. When opened it fetches the
 * *effective* card (defaults merged with any override on disk) and
 * renders it as labelled number inputs grouped into General /
 * Qualitative fallback / Phase base hours / Rules — consistent with
 * the rest of the Configuration page (no raw JSON). Save PUTs to
 * `/api/effort-card`; Reset DELETEs the override file.
 */
type RateCard = {
  version?: number;
  team_velocity?: number;
  confidence_p50_to_p90_multiplier?: number;
  qualitative?: Record<string, number>;
  phases?: Record<string, { base_hours?: number }>;
  rules?: Record<string, Record<string, number>>;
};

function humanize(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function NumberField({
  label,
  value,
  step = 0.1,
  min = 0,
  onChange,
  hint,
}: {
  label: string;
  value: number | undefined;
  step?: number;
  min?: number;
  onChange: (v: number) => void;
  hint?: string;
}): JSX.Element {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
        margin: "0.25rem 0",
      }}
    >
      <span style={{ minWidth: 220, fontSize: 13 }}>
        {label}
        {hint && (
          <span className="small muted" style={{ marginLeft: 4 }}>
            ({hint})
          </span>
        )}
      </span>
      <input
        type="number"
        step={step}
        min={min}
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? 0 : Number(v));
        }}
        style={{ width: 110, padding: "2px 6px", fontSize: 13 }}
      />
    </label>
  );
}

function EffortCardEditor(): JSX.Element {
  const [info, setInfo] = useState<EffortCardResponse | null>(null);
  const [card, setCard] = useState<RateCard | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = async () => {
    try {
      const r = await apiGetEffortCard();
      setInfo(r);
      setCard(r.card as RateCard);
    } catch (e) {
      setMsg(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoaded(true);
    }
  };

  const onToggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (e.currentTarget.open && !loaded) load();
  };

  const onSave = async () => {
    if (!card) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPutEffortCard(card as Record<string, unknown>);
      setMsg(`Saved to ${r.saved_to}`);
      await load();
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onReset = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await apiResetEffortCard();
      setMsg("Override removed — shipped defaults are now in effect.");
      await load();
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const setTop = <K extends keyof RateCard>(k: K, v: RateCard[K]) => {
    if (!card) return;
    setCard({ ...card, [k]: v });
  };

  const setQual = (key: string, v: number) => {
    if (!card) return;
    setCard({
      ...card,
      qualitative: { ...(card.qualitative ?? {}), [key]: v },
    });
  };

  const setPhase = (phase: string, v: number) => {
    if (!card) return;
    setCard({
      ...card,
      phases: {
        ...(card.phases ?? {}),
        [phase]: { ...(card.phases?.[phase] ?? {}), base_hours: v },
      },
    });
  };

  const setRule = (rule: string, unit: string, v: number) => {
    if (!card) return;
    setCard({
      ...card,
      rules: {
        ...(card.rules ?? {}),
        [rule]: { ...(card.rules?.[rule] ?? {}), [unit]: v },
      },
    });
  };

  return (
    <details
      onToggle={onToggle}
      style={{
        marginTop: "1rem",
        border: "1px solid var(--border, #ddd)",
        borderRadius: 6,
        padding: "0.5rem 0.75rem",
      }}
    >
      <summary style={{ cursor: "pointer", userSelect: "none" }}>
        Effort card (advanced){" "}
        <span className="small muted">
          — tune the hours-per-unit coefficients used by the runbook
          effort estimator. <HelpLink slug="19-effort" />
        </span>
      </summary>
      <div style={{ marginTop: "0.5rem" }}>
        {!loaded && <div className="small muted">Loading…</div>}
        {info && card && (
          <>
            <p className="small muted" style={{ marginTop: 0 }}>
              Current source:{" "}
              {info.is_default ? (
                <span className="pill info">shipped default</span>
              ) : (
                <code>{info.source}</code>
              )}
              . Version {card.version ?? 1}. Edits are validated against
              the same Pydantic model the analyzer uses.
            </p>

            <fieldset style={{ border: "1px solid var(--border, #eee)", borderRadius: 4, padding: "0.5rem 0.75rem", marginTop: "0.5rem" }}>
              <legend className="small">General</legend>
              <NumberField
                label="Team velocity"
                value={card.team_velocity}
                step={0.1}
                hint="1.0 = default speed; 2.0 = twice as fast"
                onChange={(v) => setTop("team_velocity", v)}
              />
              <NumberField
                label="P90 multiplier"
                value={card.confidence_p50_to_p90_multiplier}
                step={0.1}
                hint="P90 = P50 × this"
                onChange={(v) => setTop("confidence_p50_to_p90_multiplier", v)}
              />
            </fieldset>

            <fieldset style={{ border: "1px solid var(--border, #eee)", borderRadius: 4, padding: "0.5rem 0.75rem", marginTop: "0.5rem" }}>
              <legend className="small">
                Qualitative fallback (hours when no rule matches)
              </legend>
              {(["low", "medium", "high"] as const).map((k) => (
                <NumberField
                  key={k}
                  label={humanize(k)}
                  value={card.qualitative?.[k]}
                  step={0.5}
                  onChange={(v) => setQual(k, v)}
                />
              ))}
            </fieldset>

            <fieldset style={{ border: "1px solid var(--border, #eee)", borderRadius: 4, padding: "0.5rem 0.75rem", marginTop: "0.5rem" }}>
              <legend className="small">
                Phase base hours (fixed overhead per phase)
              </legend>
              {Object.keys(card.phases ?? {}).map((phase) => (
                <NumberField
                  key={phase}
                  label={humanize(phase)}
                  value={card.phases?.[phase]?.base_hours}
                  step={1}
                  onChange={(v) => setPhase(phase, v)}
                />
              ))}
            </fieldset>

            <fieldset style={{ border: "1px solid var(--border, #eee)", borderRadius: 4, padding: "0.5rem 0.75rem", marginTop: "0.5rem" }}>
              <legend className="small">
                Rules (hours per unit, capped where applicable)
              </legend>
              {Object.keys(card.rules ?? {}).map((rule) => {
                const coeffs = card.rules?.[rule] ?? {};
                return (
                  <div
                    key={rule}
                    style={{
                      borderTop: "1px dashed var(--border, #eee)",
                      paddingTop: "0.4rem",
                      marginTop: "0.4rem",
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13 }}>
                      <code>{rule}</code>
                    </div>
                    {Object.keys(coeffs).map((unit) => (
                      <NumberField
                        key={unit}
                        label={humanize(unit)}
                        value={coeffs[unit]}
                        step={unit === "cap_hours" ? 5 : 0.25}
                        onChange={(v) => setRule(rule, unit, v)}
                      />
                    ))}
                  </div>
                );
              })}
            </fieldset>

            <div className="actions" style={{ marginTop: "0.75rem" }}>
              <button onClick={onSave} disabled={busy}>
                {busy ? "Saving…" : "Save effort card"}
              </button>
              <button
                onClick={onReset}
                disabled={busy || info.is_default}
                title={
                  info.is_default
                    ? "Already using shipped defaults"
                    : "Delete the override file"
                }
              >
                Reset to shipped defaults
              </button>
              {msg && <span className="muted small">{msg}</span>}
            </div>

            {info.available_rule_keys.length > 0 && (
              <details style={{ marginTop: "0.5rem" }}>
                <summary className="small muted">
                  Recognised rule keys ({info.available_rule_keys.length})
                </summary>
                <ul className="small">
                  {info.available_rule_keys.map((k) => (
                    <li key={k}><code>{k}</code></li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>
    </details>
  );
}


/**
 * Collapsible **Backup & restore** panel.
 *
 * Lets the user download every run under the server's `runs_dir` as a
 * single zip, and re-import a previously exported zip. Secrets in
 * `.env` are never included � the archiver only walks `runs_dir` and
 * also drops dotfiles defensively. The import flow validates the zip
 * before extracting and supports three conflict policies.
 */
function RunsBackupRestore(): JSX.Element {
  const [busy, setBusy] = useState<"export" | "import" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<RunsImportMode>("skip_existing");
  const [result, setResult] = useState<RunsImportResult | null>(null);

  const onExport = async () => {
    setBusy("export");
    setError(null);
    setMsg(null);
    try {
      await apiExportRunsArchive();
      setMsg("Download started.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const onImport = async () => {
    if (!file) return;
    setBusy("import");
    setError(null);
    setMsg(null);
    setResult(null);
    try {
      const r = await apiImportRunsArchive(file, mode);
      setResult(r);
      const parts: string[] = [];
      if (r.imported.length) parts.push(`${r.imported.length} imported`);
      if (r.skipped.length) parts.push(`${r.skipped.length} skipped`);
      const renamedCount = Object.keys(r.renamed).length;
      if (renamedCount) parts.push(`${renamedCount} renamed`);
      setMsg(parts.length ? parts.join(", ") : "Nothing to import.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <details className="card" style={{ marginTop: "1rem" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Backup &amp; restore <span className="muted small">(export / import all run data)</span>
      </summary>
      <div style={{ marginTop: "0.75rem" }}>
        <p className="small muted" style={{ marginTop: 0 }}>
          Download every run under <code>runs/</code> as a single zip, or restore
          one on another machine. The export <strong>never includes</strong>{" "}
          <code>.env</code> or other secrets � only run artefacts.
        </p>

        <div className="actions" style={{ gap: 8, flexWrap: "wrap" }}>
          <button onClick={onExport} disabled={busy !== null}>
            {busy === "export" ? "Preparing�" : "Download all run data (.zip)"}
          </button>
        </div>

        <div
          className="form-grid"
          style={{ marginTop: "0.75rem", alignItems: "end" }}
        >
          <label>
            <span>Import zip file</span>
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            <span>On conflict</span>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as RunsImportMode)}
            >
              <option value="skip_existing">Skip existing</option>
              <option value="overwrite">Overwrite</option>
              <option value="rename">Rename incoming run</option>
            </select>
          </label>
          <button
            onClick={onImport}
            disabled={busy !== null || file === null}
            style={{ alignSelf: "end" }}
          >
            {busy === "import" ? "Importing�" : "Import"}
          </button>
        </div>

        {msg && (
          <p className="small" style={{ marginTop: "0.5rem" }}>
            {msg}
          </p>
        )}
        {error && (
          <p className="small" style={{ marginTop: "0.5rem", color: "var(--err, #c33)" }}>
            {error}
          </p>
        )}
        {result && (result.imported.length > 0 || Object.keys(result.renamed).length > 0) && (
          <details className="small" style={{ marginTop: "0.5rem" }}>
            <summary>Import details</summary>
            {result.imported.length > 0 && (
              <div>
                <strong>Imported:</strong>
                <ul>
                  {result.imported.map((id) => (
                    <li key={id}><code>{id}</code></li>
                  ))}
                </ul>
              </div>
            )}
            {Object.keys(result.renamed).length > 0 && (
              <div>
                <strong>Renamed:</strong>
                <ul>
                  {Object.entries(result.renamed).map(([oldId, newId]) => (
                    <li key={oldId}>
                      <code>{oldId}</code> ? <code>{newId}</code>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {result.warnings.length > 0 && (
              <div>
                <strong>Warnings:</strong>
                <ul>
                  {result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
          </details>
        )}

        <p className="small muted" style={{ marginTop: "0.75rem" }}>
          The archive contains <code>manifest.json</code> plus{" "}
          <code>runs/&lt;id&gt;/</code> trees only. Dotfiles and symlinks are
          dropped on export and rejected on import; path-traversal and
          zip-bomb entries are blocked.
        </p>
      </div>
    </details>
  );
}
