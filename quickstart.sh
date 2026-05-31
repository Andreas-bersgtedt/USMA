#!/usr/bin/env bash
# Quickstart bootstrapper for Unified Solution Migration Analyzer (USMA).
# Mirror of quickstart.ps1 for Linux and macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Andreas-bersgtedt/USMA/main/quickstart.sh | bash
#   # or, from inside an already-cloned working tree:
#   ./quickstart.sh --skip-clone
#
# Flags:
#   --install-root DIR    Where to clone the repo (default: $PWD).
#   --repo public|private Public or private fork (default: public).
#   --repo-url URL        Explicit git URL (overrides --repo).
#   --branch NAME         Branch / tag to check out (default: main).
#   --python EXE          Python interpreter (default: python3.12 if found, else python3).
#   --skip-clone          Run from the current working tree.
#   --skip-web-build      Skip `npm ci && npm run build`.
#   --skip-doctor         Skip the final `sma doctor --offline` check.
#   --no-serve            Do not launch `sma serve --with-api` at the end.
#   -h, --help            Show this help and exit.
#
# Installs all optional pip extras so the FastAPI control plane, every
# analyzer module, and the dev / test toolchain are ready out of the box.
set -euo pipefail

INSTALL_ROOT="$(pwd)"
REPO="public"
REPO_URL=""
BRANCH="main"
PYTHON_EXE=""
SKIP_CLONE=0
SKIP_WEB_BUILD=0
SKIP_DOCTOR=0
NO_SERVE=0

EXTRAS=("dev" "cost" "web" "databricks" "bigquery" "snowflake")

usage() { sed -n '2,22p' "$0"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-root)    INSTALL_ROOT="$2"; shift 2 ;;
        --repo)            REPO="$2"; shift 2 ;;
        --repo-url)        REPO_URL="$2"; shift 2 ;;
        --branch)          BRANCH="$2"; shift 2 ;;
        --python)          PYTHON_EXE="$2"; shift 2 ;;
        --skip-clone)      SKIP_CLONE=1; shift ;;
        --skip-web-build)  SKIP_WEB_BUILD=1; shift ;;
        --skip-doctor)     SKIP_DOCTOR=1; shift ;;
        --no-serve)        NO_SERVE=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "Unknown flag: $1" >&2; usage; exit 2 ;;
    esac
done

if [[ -z "$REPO_URL" ]]; then
    case "$REPO" in
        public)  REPO_URL="https://github.com/Andreas-bersgtedt/USMA.git" ;;
        private) REPO_URL="https://github.com/anbergst_microsoft/USMA.git" ;;
        *) echo "--repo must be 'public' or 'private'" >&2; exit 2 ;;
    esac
fi

cyan() { printf '\033[36m==> %s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        red "Required command '$1' not found on PATH. $2"
        exit 1
    fi
}

# --------------------------------------------------------------------------- #
# 1. Prerequisites
# --------------------------------------------------------------------------- #
cyan "Checking host prerequisites"
require_cmd git "Install git via your package manager (e.g. 'sudo apt-get install -y git' or 'brew install git')."

if [[ -z "$PYTHON_EXE" ]]; then
    if command -v python3.12 >/dev/null 2>&1; then PYTHON_EXE="python3.12";
    elif command -v python3 >/dev/null 2>&1;    then PYTHON_EXE="python3";
    else red "No python3.12 / python3 found on PATH."; exit 1; fi
fi
PY_VER="$("$PYTHON_EXE" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! "$PYTHON_EXE" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,12) else 1)'; then
    red "USMA requires Python >= 3.12, but '$PYTHON_EXE' is $PY_VER."
    exit 1
fi
echo "    python: $PYTHON_EXE ($PY_VER)"

if [[ $SKIP_WEB_BUILD -eq 0 ]]; then
    require_cmd node "Install Node.js 18+ (e.g. via nvm) or pass --skip-web-build."
    require_cmd npm  "Install npm (bundled with Node.js) or pass --skip-web-build."
fi

# Heads-up for SQL workloads on Linux (warn-only).
if [[ "$(uname -s)" == "Linux" ]] && ! command -v odbcinst >/dev/null 2>&1; then
    yellow "    note: unixODBC not detected. SQL Server analyzers need the Microsoft ODBC Driver 18."
    yellow "          See QUICKSTART.md (Linux section) for the apt install commands."
fi

# --------------------------------------------------------------------------- #
# 2. Clone (or reuse) the repo
# --------------------------------------------------------------------------- #
mkdir -p "$INSTALL_ROOT"
cd "$INSTALL_ROOT"

REPO_DIR="$INSTALL_ROOT/USMA"
if [[ $SKIP_CLONE -eq 1 ]]; then
    cyan "Skipping clone (--skip-clone); using current directory"
    REPO_DIR="$(pwd)"
elif [[ -d "$REPO_DIR/.git" ]]; then
    cyan "Repo already present at $REPO_DIR — fetching and switching to '$BRANCH'"
    cd "$REPO_DIR"
    git fetch --tags --prune origin
    git checkout "$BRANCH"
    git pull --ff-only origin "$BRANCH" || true
else
    cyan "Cloning $REPO_URL (branch: $BRANCH)"
    git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
    cd "$REPO_DIR"
fi

# --------------------------------------------------------------------------- #
# 3. Virtualenv
# --------------------------------------------------------------------------- #
if [[ -d .venv ]]; then
    cyan "Reusing existing venv at .venv"
else
    cyan "Creating virtual environment in .venv"
    "$PYTHON_EXE" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel

# --------------------------------------------------------------------------- #
# 4. Install the package + extras
# --------------------------------------------------------------------------- #
EXTRAS_JOINED="$(IFS=,; echo "${EXTRAS[*]}")"
cyan "pip install -e .[${EXTRAS_JOINED}]"
pip install -e ".[${EXTRAS_JOINED}]"

# --------------------------------------------------------------------------- #
# 5. Build the SPA bundle
# --------------------------------------------------------------------------- #
if [[ $SKIP_WEB_BUILD -eq 0 ]]; then
    cyan "Building web SPA (web/dist)"
    pushd web >/dev/null
    if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
    npm run build
    popd >/dev/null
else
    yellow "    --skip-web-build: not building web/ (sma serve --with-api will have no UI)"
fi

# --------------------------------------------------------------------------- #
# 6. Smoke test
# --------------------------------------------------------------------------- #
if [[ $SKIP_DOCTOR -eq 0 ]]; then
    cyan "Running 'sma doctor --offline'"
    sma doctor --offline || {
        red "sma doctor reported failures — fix them before running analyzers."
        exit 1
    }
fi

# --------------------------------------------------------------------------- #
# 7. Launch the control plane
# --------------------------------------------------------------------------- #
if [[ $NO_SERVE -eq 0 && -d web/dist ]]; then
    cyan "Starting 'sma serve --with-api --static-dir web/dist'  (Ctrl+C to stop)"
    exec sma serve --with-api --static-dir web/dist
else
    cyan "Bootstrap complete."
    echo "    Activate later with:  source .venv/bin/activate"
    echo "    Start the control plane with:  sma serve --with-api --static-dir web/dist"
fi
