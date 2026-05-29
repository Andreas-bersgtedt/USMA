# Dependency Inventory

This document inventories the third-party dependencies used by **Synapse Migration
Analyzer**. All declarations are sourced from [pyproject.toml](pyproject.toml); this
file is a human-readable companion intended for license / supply-chain review.

To regenerate a resolved list with exact pinned versions in your environment:

```powershell
pip install -e ".[dev]"
pip freeze > requirements.lock.txt
```

## Runtime dependencies (Python)

Declared under `[project].dependencies` in [pyproject.toml](pyproject.toml).

| Package | Version constraint | Purpose | License | Project URL |
| --- | --- | --- | --- | --- |
| [azure-identity](https://pypi.org/project/azure-identity/) | `>=1.17` | Service-principal / interactive Azure AD auth used by [auth.py](src/usma/auth.py). | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-mgmt-synapse](https://pypi.org/project/azure-mgmt-synapse/) | `>=2.0.0` | ARM control-plane client for Synapse workspaces and pool inventory. | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-mgmt-resource](https://pypi.org/project/azure-mgmt-resource/) | `>=23.1` | Generic ARM resource enumeration (subscriptions, resource groups). | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-mgmt-monitor](https://pypi.org/project/azure-mgmt-monitor/) | `>=6.0.2` | Azure Monitor metrics for the `monitoring` and `storage` modules. | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-mgmt-storage](https://pypi.org/project/azure-mgmt-storage/) | `>=21.1.0` | ADLS Gen2 / Storage account inventory for the `storage` module. | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-synapse-artifacts](https://pypi.org/project/azure-synapse-artifacts/) | `>=0.20` | Pipelines, notebooks, and Spark Job Definition inventory. | MIT | https://github.com/Azure/azure-sdk-for-python |
| [azure-synapse-spark](https://pypi.org/project/azure-synapse-spark/) | `>=0.7` | Spark Livy job-history collection (interactive sessions + scheduled batches) used by the `spark_pools` module. | MIT | https://github.com/Azure/azure-sdk-for-python |
| [pyodbc](https://pypi.org/project/pyodbc/) | `>=5.1` | DMV-driven SQL collection for dedicated and serverless SQL pools. Requires the **Microsoft ODBC Driver 18 for SQL Server** (see below). | MIT-0 | https://github.com/mkleehammer/pyodbc |
| [pydantic](https://pypi.org/project/pydantic/) | `>=2.7` | Typed models in every module's `models.py`. | MIT | https://github.com/pydantic/pydantic |
| [rich](https://pypi.org/project/rich/) | `>=13.7` | Console output (progress, tables) used by the CLI. | MIT | https://github.com/Textualize/rich |
| [click](https://pypi.org/project/click/) | `>=8.1` | CLI framework backing the `sma` entry point. | BSD-3-Clause | https://github.com/pallets/click |
| [jinja2](https://pypi.org/project/Jinja2/) | `>=3.1` | HTML report templating. | BSD-3-Clause | https://github.com/pallets/jinja |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | `>=1.0` | Loads `.env` configuration in [config.py](src/usma/config.py). | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| [six](https://pypi.org/project/six/) | `>=1.16` | Transitive dep retained explicitly because some `azure-mgmt-synapse` model paths still import it. | MIT | https://github.com/benjaminp/six |
| [sqlglot](https://pypi.org/project/sqlglot/) | `>=23.0` | AST-based T-SQL parser used by the dedicated-pool "top consumed tables" collector to resolve referenced tables / views in submitted workload commands. | MIT | https://github.com/tobymao/sqlglot |

## Development dependencies (Python)

Declared under `[project.optional-dependencies].dev` in [pyproject.toml](pyproject.toml).
Install with `pip install -e ".[dev]"`.

| Package | Version constraint | Purpose | License |
| --- | --- | --- | --- |
| [pytest](https://pypi.org/project/pytest/) | `>=8.0` | Test runner for the `tests/` suite. | MIT |
| [pytest-cov](https://pypi.org/project/pytest-cov/) | `>=5.0` | Coverage plugin for pytest. | MIT |
| [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) | `>=0.23` | Async test support for the FastAPI control-plane tests in [tests/web/](tests/web). | Apache-2.0 |
| [ruff](https://pypi.org/project/ruff/) | `>=0.5` | Linter / formatter (config in `[tool.ruff]`). | MIT |
| [mypy](https://pypi.org/project/mypy/) | `>=1.10` | Static type checking. | MIT |
| [fastapi](https://pypi.org/project/fastapi/) | `>=0.115` | (Mirrored from `[web]`) Used by the control-plane test suite. | MIT |
| [httpx](https://pypi.org/project/httpx/) | `>=0.27` | (Mirrored from `[web]`) Backs `fastapi.testclient.TestClient`. | BSD-3-Clause |
| [sse-starlette](https://pypi.org/project/sse-starlette/) | `>=2.1` | (Mirrored from `[web]`) Used by the SSE endpoint tests. | BSD-3-Clause |

## Optional dependencies (Python)

### `[web]` — local control plane (`sma serve --with-api`)

Declared under `[project.optional-dependencies].web` in [pyproject.toml](pyproject.toml).
Install with `pip install -e ".[web]"`. Without these, `sma serve` still works
as a static file server, but `--with-api` raises a clear install error.

| Package | Version constraint | Purpose | License | Project URL |
| --- | --- | --- | --- | --- |
| [fastapi](https://pypi.org/project/fastapi/) | `>=0.115` | HTTP framework backing the `/api/*` endpoints in [src/.../web/](src/usma/web). | MIT | https://github.com/fastapi/fastapi |
| [uvicorn[standard]](https://pypi.org/project/uvicorn/) | `>=0.30` | ASGI server (`sma serve --with-api` runs `uvicorn.run(app, ...)`). | BSD-3-Clause | https://github.com/encode/uvicorn |
| [sse-starlette](https://pypi.org/project/sse-starlette/) | `>=2.1` | Server-Sent Events helper used by `GET /api/runs/<id>/events`. | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| [httpx](https://pypi.org/project/httpx/) | `>=0.27` | HTTP client used internally by FastAPI's TestClient and by future federated-mode hooks. | BSD-3-Clause | https://github.com/encode/httpx |

### `[cost]` — live Cost Management queries

| Package | Version constraint | Purpose | License |
| --- | --- | --- | --- |
| [azure-mgmt-costmanagement](https://pypi.org/project/azure-mgmt-costmanagement/) | `>=4.0` | Live consumption queries for `sma analyze-cost`. Without it, the analyzer still runs but emits a `cost.sdk_missing` finding. | MIT |

## Build system

Declared under `[build-system]` in [pyproject.toml](pyproject.toml).

| Package | Purpose | License |
| --- | --- | --- |
| [hatchling](https://pypi.org/project/hatchling/) | PEP 517 build backend (`hatchling.build`). | MIT |

## External (non-PyPI) prerequisites

| Component | Purpose | Where it's needed |
| --- | --- | --- |
| **Microsoft ODBC Driver 18 for SQL Server** | Native driver loaded by `pyodbc` to talk to dedicated and serverless SQL pools. Install via the [Microsoft download page](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server). | All commands that hit a SQL pool: `analyze-dedicated-pools`, `analyze-serverless-pools`, parts of `map-to-fabric`, and `sma doctor`. |
| **Python 3.12+** | Minimum interpreter required (`requires-python = ">=3.12"`). | All commands. |
| **Azure service principal / identity** | Reader + Monitoring Reader + Synapse Artifact User + per-pool SQL access (see [README.md](README.md#prerequisites)). | All `analyze-*` commands. |

Reports are fully self-contained HTML — there are no runtime CDN, JavaScript, or CSS
dependencies fetched at view time.

## Reporting issues with dependencies

- Vulnerabilities **in this repository's code**: see [SECURITY.md](SECURITY.md).
- Vulnerabilities **in upstream packages**: please report to the respective project
  using the URLs above. We track and bump versions reactively.
