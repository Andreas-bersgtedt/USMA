# Glossary — acronyms & abbreviations

Reference for terms used across the README, QUICKSTART, CHANGELOG and the
rest of the user guide. Entries reflect how the term is used **in this
project** — industry-standard meanings are noted where they differ.

| Acronym | Meaning in this project |
|---|---|
| AAD | Azure Active Directory (now Microsoft Entra ID) — service-principal / federated identity |
| ACCOUNT_USAGE | Snowflake system database (`SNOWFLAKE.ACCOUNT_USAGE`) — source of metering & query history |
| ADC | Application Default Credentials — Google Cloud auth path used by the BigQuery source |
| ADF | Azure Data Factory — source type analyzed alongside Synapse |
| ADLS | Azure Data Lake Storage (Gen2) — storage scoped to Synapse workspaces |
| ADR | Architecture Decision Record (`docs/adr/`) |
| ARM | Azure Resource Manager — control-plane API used for discovery |
| ASA | Azure Synapse Analytics — primary migration source |
| AWS | Amazon Web Services — Databricks / Snowflake host cloud |
| BQ | BigQuery |
| CCI | Clustered Columnstore Index (Synapse SQL) |
| CLI | Command-line interface (`sma` / `usma`) |
| CMK | Customer-Managed Keys — surfaced by the security module |
| CSRF | Cross-Site Request Forgery — mitigated via the `X-SMA-API` header |
| CTE | Common Table Expression |
| CU | Capacity Unit — Fabric compute metric; basis for F-SKU recommendations |
| DBU | Databricks Unit — converted to vCore-hours then Fabric CU-hours |
| DIU | Data Integration Unit — ADF / Synapse pipeline compute metric |
| DLT | Delta Live Tables (Databricks) |
| DMV | Dynamic Management View (`sys.dm_pdw_*` in Synapse) |
| DWU | Data Warehouse Unit — dedicated Synapse SQL pool capacity |
| ETL | Extract / Transform / Load |
| F2 … F2048 | Fabric capacity SKUs |
| FQDN | Fully Qualified Domain Name |
| GCP | Google Cloud Platform |
| GCS | Google Cloud Storage |
| IAM | Identity and Access Management (Google Cloud) |
| IR | Integration Runtime (ADF / Synapse); self-hosted IRs flagged as migration blockers |
| JAR | Java Archive — Databricks task type |
| JDBC | Java Database Connectivity |
| JWT | JSON Web Token — Snowflake key-pair auth (superseded by OAuth in 7-E.1) |
| KMS | Key Management Service |
| LDAP | Lightweight Directory Access Protocol |
| M2M | Machine-to-Machine OAuth grant (Databricks / Snowflake) |
| MDF | Mapping Data Flow (ADF / Synapse) |
| METERING_HISTORY | Snowflake table — credits per warehouse over time |
| MPE | Managed Private Endpoints |
| MSI | Managed Service Identity (Azure) |
| Non-Azure scope | A scope whose primary source is BigQuery, Snowflake-on-AWS, or Databricks-on-AWS/GCP. These scopes have no Azure tenant / subscription / resource group and are grouped under `— · —` in the Estate Overview. |
| ODBC | Open Database Connectivity — Microsoft ODBC Driver 17/18 for Synapse SQL |
| OAuth | Authorisation protocol — Snowflake refresh-token flow, Google ADC browser sign-in |
| PAT | Personal Access Token (Databricks) |
| PII | Personally Identifiable Information — never collected by the analyzer |
| QUERY_HISTORY | Snowflake table — completed jobs (workload + cost basis) |
| RBAC | Role-Based Access Control (Azure / Synapse / Databricks) |
| RI | Reserved Instance (Fabric / Azure pricing) |
| Run attribution | The `tenant_id` / `subscription_id` / `resource_group` / `workspace_name` fields persisted on each `run.json` and used by the Estate Overview to group runs by `Cloud → Tenant · Subscription`. Non-Azure scopes (BigQuery, Snowflake-on-AWS, Databricks-on-AWS/GCP) leave the Azure fields `null`; pre-5.3.3 runs may need `sma migrate-run-attribution`. |
| SCIM | System for Cross-domain Identity Management (Databricks roles) |
| SJD | Spark Job Definition (Synapse) |
| SKU | Stock Keeping Unit — Fabric F-SKUs |
| SMA | Synapse Migration Analyzer — legacy acronym (now USMA); `sma` CLI still aliased |
| SPA | Single-Page Application — React UI at `web/dist/` |
| SSE | Server-Sent Events — run progress streaming |
| SSIS | SQL Server Integration Services (future source) |
| T-SQL | Transact-SQL (Synapse / SQL Server dialect) |
| TCO | Total Cost of Ownership |
| TDE | Transparent Data Encryption |
| TLS | Transport Layer Security (TLS 1.2+ enforced by ODBC) |
| UDF | User-Defined Function |
| Unity Catalog | Databricks governance layer (cost / DBU attribution) |
| USAGE_IN_CURRENCY_DAILY | Snowflake table — $/credit rate + storage cost |
| USMA | Unified Solution Migration Analyzer (the tool) |
| vCore | Virtual core — common normalisation unit between DBU / DIU / Snowflake credits and Fabric CU-hours |
| VPC | Virtual Private Cloud (AWS) |
| XDG | X Desktop Group Base Directory spec — Linux / macOS cache / config / data layout (5.3.0) |
| X-SMA-API | HTTP header (`X-SMA-API: 1`) required on all state-changing API requests |
