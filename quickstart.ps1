<#
.SYNOPSIS
    Quickstart bootstrapper for Unified Solution Migration Analyzer (QUICKSTART.md section 0.2).

.DESCRIPTION
    Automates the "Clone & install" section:
      1. Clone the USMA repo (skipped if already present)
      2. Create a Python virtual environment under .venv
      3. Activate the venv in the *current* PowerShell session
      4. pip install -e ".[dev]"
      5. Verify the `sma` CLI is on the path (sma --version / sma --help)

    Run with dot-sourcing so the venv stays activated in your shell:
        . .\quickstart.ps1
    or just:
        .\quickstart.ps1
    (a child shell will be activated; close it to deactivate).

.PARAMETER InstallRoot
    Directory under which the repo will be cloned. Defaults to the current
    directory.

.PARAMETER Repo
    Which fork of the repository to clone:
      - `Public`  -> https://github.com/Andreas-bersgtedt/USMA.git (default)
      - `Private` -> https://github.com/anbergst_microsoft/USMA.git

    The repository was renamed from `SynapseMigrationAnalyzer` to `USMA` in
    Phase 3 (ADR-0004). GitHub keeps the old URL redirecting indefinitely,
    so existing clones of `SynapseMigrationAnalyzer.git` continue to work
    against the renamed remote, but new clones should use the new name.

    Ignored when `-RepoUrl` is supplied explicitly.

.PARAMETER RepoUrl
    Git URL of the repo. When supplied, overrides `-Repo`. Defaults to the URL
    derived from `-Repo` (Public).

.PARAMETER PythonExe
    Python interpreter to use for the venv. Defaults to `python`.

.PARAMETER SkipClone
    Skip the `git clone` step (use when running from inside an already-cloned
    working tree).

.PARAMETER Branch
    Branch / tag to check out after clone. When omitted, the script lists the
    remote branches and prompts interactively with a 10-second timeout that
    defaults to `main`.

.PARAMETER NonInteractive
    Skip the interactive branch prompt and use `Branch` (or `main`) directly.

.PARAMETER SkipDoctor
    Skip the final `sma doctor --offline` smoke-test. The smoke test runs
    by default since v1.2.x because it is offline / read-only.

.PARAMETER SkipWebBuild
    Skip the `npm install` + `npm run build` step that produces `web/dist`.
    By default the SPA bundle is built so `sma serve --with-api` has a UI to
    mount. Pass this switch on hosts without Node.js when you don't need the
    browser control plane.

.PARAMETER NoServe
    Skip the final `sma serve --with-api --static-dir web/dist` launch.
    By default, when `sma doctor --offline` succeeds and `web/dist` exists,
    the script ends by starting the local control-plane server in the
    foreground (Ctrl+C to stop).

.NOTES
    All optional pip extras (`dev`, `cost`, `web`) are always installed so
    every analyzer module, the FastAPI control plane (`sma serve --with-api`),
    and the dev / test toolchain are ready out of the box. The web SPA in
    `web/` is also built by default (requires Node.js / npm on PATH).

.EXAMPLE
    PS> .\quickstart.ps1

.EXAMPLE
    PS> .\quickstart.ps1 -Repo Private

.EXAMPLE
    PS> .\quickstart.ps1 -InstallRoot C:\src -PythonExe py

.EXAMPLE
    PS> .\quickstart.ps1 -Branch feature/run-history
#>
[CmdletBinding()]
param(
    [string]$InstallRoot     = (Get-Location).Path,
    [ValidateSet('Public', 'Private')]
    [string]$Repo            = 'Public',
    [string]$RepoUrl,
    [string]$PythonExe       = 'python',
    [switch]$SkipClone,
    [string]$Branch,
    [switch]$NonInteractive,
    [switch]$SkipDoctor,
    [switch]$SkipWebBuild,
    [switch]$NoServe
)

# All optional extras defined in pyproject.toml are mandatory: the quickstart
# bootstraps a fully-featured environment (CLI + control-plane API + dev tools).
$Extras = @('dev', 'cost', 'web')

# Capture the directory the user launched the script from BEFORE any
# Set-Location call moves us into the cloned repo. Used by the .env
# bootstrap step to copy a pre-existing .env from the launch dir.
$LaunchDir = (Get-Location).Path

if (-not $RepoUrl) {
    $RepoUrl = if ($Repo -eq 'Private') {
        'https://github.com/anbergst_microsoft/USMA.git'
    } else {
        'https://github.com/Andreas-bersgtedt/USMA.git'
    }
}

$ErrorActionPreference = 'Stop'

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Assert-Command([string]$name, [string]$hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required command '$name' not found on PATH. $hint"
    }
}

# Lists remote branches of $RepoUrl and prompts the user to pick one. Times out
# after $TimeoutSeconds of inactivity and falls back to $DefaultBranch.
function Select-RemoteBranch {
    param(
        [Parameter(Mandatory)][string]$RepoUrl,
        [string]$DefaultBranch = 'main',
        [int]$TimeoutSeconds   = 10
    )

    Write-Host "    listing branches on remote ..." -ForegroundColor DarkGray
    $raw = git ls-remote --heads $RepoUrl 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        Write-Host "    (could not list remote branches; defaulting to '$DefaultBranch')" -ForegroundColor Yellow
        return $DefaultBranch
    }

    $branches = $raw |
        ForEach-Object { ($_ -split "`t")[1] } |
        Where-Object   { $_ -like 'refs/heads/*' } |
        ForEach-Object { $_ -replace '^refs/heads/', '' } |
        Sort-Object -Unique

    if (-not $branches -or $branches.Count -eq 0) {
        return $DefaultBranch
    }

    # Move default to position 1 if present.
    if ($branches -contains $DefaultBranch) {
        $branches = @($DefaultBranch) + ($branches | Where-Object { $_ -ne $DefaultBranch })
    }

    Write-Host ""
    Write-Host "    Available branches:" -ForegroundColor Cyan
    for ($i = 0; $i -lt $branches.Count; $i++) {
        $marker = if ($i -eq 0) { ' (default)' } else { '' }
        Write-Host ("      {0,2}. {1}{2}" -f ($i + 1), $branches[$i], $marker)
    }
    Write-Host ""
    Write-Host "    Enter number or branch name (timeout ${TimeoutSeconds}s -> '$DefaultBranch'): " -NoNewline -ForegroundColor Yellow

    # Poll the keyboard so we can time out without blocking on Read-Host.
    # If the host doesn't expose a keyboard (eg. ISE, redirected stdin), fall back.
    if (-not [Environment]::UserInteractive -or $null -eq $Host.UI.RawUI -or
        $Host.Name -eq 'Windows PowerShell ISE Host') {
        Write-Host "(non-interactive host; defaulting)" -ForegroundColor DarkGray
        return $DefaultBranch
    }

    $buffer = ''
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'Enter') {
                Write-Host ''
                break
            }
            if ($key.Key -eq 'Backspace') {
                if ($buffer.Length -gt 0) {
                    $buffer = $buffer.Substring(0, $buffer.Length - 1)
                    Write-Host -NoNewline "`b `b"
                }
                continue
            }
            if ($key.Key -eq 'Escape') {
                Write-Host ''
                $buffer = ''
                break
            }
            if ($key.KeyChar -and -not [char]::IsControl($key.KeyChar)) {
                $buffer += $key.KeyChar
                Write-Host -NoNewline $key.KeyChar
                # Once the user starts typing, stop the countdown.
                $deadline = [DateTime]::MaxValue
            }
        } else {
            Start-Sleep -Milliseconds 100
        }
    }

    $choice = $buffer.Trim()
    if (-not $choice) {
        Write-Host "    -> using default '$DefaultBranch'" -ForegroundColor DarkGray
        return $DefaultBranch
    }

    # Numeric selection?
    [int]$index = 0
    if ([int]::TryParse($choice, [ref]$index) -and $index -ge 1 -and $index -le $branches.Count) {
        $picked = $branches[$index - 1]
        Write-Host "    -> '$picked'" -ForegroundColor DarkGray
        return $picked
    }

    # Treat as branch name; warn if it isn't in the remote list but allow it.
    if ($branches -notcontains $choice) {
        Write-Host "    (warning: '$choice' is not in the listed branches; passing to git anyway)" -ForegroundColor Yellow
    } else {
        Write-Host "    -> '$choice'" -ForegroundColor DarkGray
    }
    return $choice
}

# --- Prereq checks (QUICKSTART section 0.1) ---------------------------------
Write-Step "Checking host prerequisites"
Assert-Command 'git'      'Install Git from https://git-scm.com/download/win and reopen PowerShell.'
Assert-Command $PythonExe 'Install Python 3.12+ from https://www.python.org/downloads/ and reopen PowerShell.'

$pyVersion = & $PythonExe --version 2>&1
Write-Host "    git    : $(git --version)"
Write-Host "    python : $pyVersion"

# Enforce >=3.12 (matches pyproject.toml requires-python). Fail fast with a
# clear message rather than letting `pip install` blow up later.
$pyMatch = [regex]::Match([string]$pyVersion, '(\d+)\.(\d+)(?:\.(\d+))?')
if (-not $pyMatch.Success) {
    throw "Could not parse Python version from '$pyVersion'."
}
$pyMajor = [int]$pyMatch.Groups[1].Value
$pyMinor = [int]$pyMatch.Groups[2].Value
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 12)) {
    throw ("Python $pyMajor.$pyMinor detected; this project requires Python 3.12+. " +
           "Install a newer Python from https://www.python.org/downloads/ and pass it via -PythonExe.")
}

# Soft-check: ODBC Driver 18 for SQL Server (required by pyodbc at runtime,
# but not by pip install). Warn-only so users without SQL workloads can still
# get a working install.
try {
    if (Get-Command Get-OdbcDriver -ErrorAction SilentlyContinue) {
        $odbc = Get-OdbcDriver -Name 'ODBC Driver 18 for SQL Server' -ErrorAction SilentlyContinue
        if (-not $odbc) {
            Write-Host "    odbc   : ODBC Driver 18 NOT installed - 'sma analyze-*' against SQL pools will fail at runtime." -ForegroundColor Yellow
            Write-Host "             Install: https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server" -ForegroundColor Yellow
        } else {
            Write-Host "    odbc   : $($odbc.Name)"
        }
    } else {
        Write-Host "    odbc   : (Get-OdbcDriver unavailable on this host; skipping check)" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "    odbc   : check failed ($($_.Exception.Message)); skipping." -ForegroundColor DarkGray
}

# --- Ensure InstallRoot exists ----------------------------------------------
if (-not (Test-Path $InstallRoot)) {
    Write-Step "Creating InstallRoot '$InstallRoot'"
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
}
$InstallRoot = (Resolve-Path $InstallRoot).Path

# --- Clone (QUICKSTART section 0.2) -----------------------------------------
$repoDir = Join-Path $InstallRoot 'USMA'

if ($SkipClone) {
    Write-Step "Skipping clone (--SkipClone)"
    if (-not (Test-Path (Join-Path $repoDir 'pyproject.toml'))) {
        # Maybe the user is already *inside* the repo dir.
        if (Test-Path (Join-Path (Get-Location).Path 'pyproject.toml')) {
            $repoDir = (Get-Location).Path
        } else {
            throw "SkipClone set but no pyproject.toml found at '$repoDir' or in the current directory."
        }
    }
} else {
    if (Test-Path $repoDir) {
        Write-Step "Repo already present at '$repoDir'"
        if ($Branch) {
            Write-Step "Switching existing clone to branch '$Branch'"
            Push-Location $repoDir
            try {
                git fetch origin --quiet
                if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE)." }
                git checkout $Branch
                if ($LASTEXITCODE -ne 0) { throw "git checkout '$Branch' failed (exit $LASTEXITCODE)." }
                git pull --ff-only
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "    (git pull --ff-only failed; staying on local '$Branch'.)" -ForegroundColor Yellow
                }
            } finally {
                Pop-Location
            }
        }
    } else {
        # Resolve which branch to clone.
        if (-not $Branch -and -not $NonInteractive) {
            Write-Step "Selecting branch to clone"
            $Branch = Select-RemoteBranch -RepoUrl $RepoUrl -DefaultBranch 'main' -TimeoutSeconds 10
        }
        if (-not $Branch) { $Branch = 'main' }

        Write-Step "Cloning $RepoUrl (branch: $Branch)"
        Push-Location $InstallRoot
        try {
            git clone --branch $Branch $RepoUrl
            if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)." }
        } finally {
            Pop-Location
        }
    }
}

if (-not (Test-Path (Join-Path $repoDir 'pyproject.toml'))) {
    throw "Expected pyproject.toml not found under '$repoDir'."
}

Set-Location $repoDir
Write-Host "    cwd    : $(Get-Location)"

# --- Virtual environment ----------------------------------------------------
$venvDir        = Join-Path $repoDir '.venv'
$activateScript = Join-Path $venvDir 'Scripts\Activate.ps1'

if (Test-Path $activateScript) {
    Write-Step "Reusing existing venv at .venv"
} else {
    Write-Step "Creating virtual environment in .venv"
    & $PythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed (exit $LASTEXITCODE)." }
}

Write-Step "Activating venv"
. $activateScript

# --- Install (editable + dev extras) ----------------------------------------
Write-Step "Upgrading pip"
python -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)." }

# Build the editable install spec from $Extras. All extras are mandatory.
$extrasJoined = ($Extras | Where-Object { $_ } | Sort-Object -Unique) -join ','
$installSpec  = ".[${extrasJoined}]"
Write-Step "pip install -e $installSpec  (extras: $extrasJoined)"
try {
    pip install -e $installSpec
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)." }
} catch {
    Write-Host "" -ForegroundColor Red
    Write-Host "    pip install failed. The .venv may be in a partial state." -ForegroundColor Red
    Write-Host "    To recover, remove it and rerun:" -ForegroundColor Red
    Write-Host "        Remove-Item -Recurse -Force '$venvDir'" -ForegroundColor Red
    Write-Host "        .\quickstart.ps1" -ForegroundColor Red
    throw
}

# --- Build the web SPA bundle (web/dist) -----------------------------------
# `sma serve --with-api --static-dir web/dist` mounts the prebuilt SPA at /,
# so build it eagerly here. Skipped only if Node/npm aren't available — pip
# install + CLI still work in that case.
$webDir = Join-Path $repoDir 'web'
if ($SkipWebBuild) {
    Write-Step "Skipping web SPA build (-SkipWebBuild)"
} elseif (Test-Path (Join-Path $webDir 'package.json')) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        $nodeVersion = (& node --version 2>$null)
        $npmVersion  = (& npm --version  2>$null)
        Write-Host "    node   : $nodeVersion"
        Write-Host "    npm    : $npmVersion"

        Push-Location $webDir
        try {
            $lockFile        = Join-Path $webDir 'package-lock.json'
            $nodeModulesDir  = Join-Path $webDir 'node_modules'
            $needsInstall    = -not (Test-Path $nodeModulesDir)
            if (-not $needsInstall -and (Test-Path $lockFile)) {
                $lockTime = (Get-Item $lockFile).LastWriteTimeUtc
                $modTime  = (Get-Item $nodeModulesDir).LastWriteTimeUtc
                if ($lockTime -gt $modTime) { $needsInstall = $true }
            }

            if ($needsInstall) {
                if (Test-Path $lockFile) {
                    Write-Step "npm ci (reproducible install from package-lock.json)"
                    npm ci
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "    (npm ci failed; falling back to 'npm install')" -ForegroundColor Yellow
                        npm install
                    }
                } else {
                    Write-Step "npm install (no package-lock.json; generates one)"
                    npm install
                }
                if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)." }
            } else {
                Write-Step "web/node_modules up to date - skipping npm install"
            }

            Write-Step "npm run build (web/dist)"
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit $LASTEXITCODE)." }
            Write-Host "    web bundle: $(Join-Path $webDir 'dist')" -ForegroundColor DarkGray
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "" -ForegroundColor Yellow
        Write-Host "    npm not found on PATH - skipping SPA build." -ForegroundColor Yellow
        Write-Host "    Install Node.js 20+ from https://nodejs.org/ then run:" -ForegroundColor Yellow
        Write-Host "        cd web; npm install; npm run build" -ForegroundColor Yellow
        Write-Host "    (the FastAPI backend still works without a built SPA, but" -ForegroundColor Yellow
        Write-Host "     'sma serve --with-api --static-dir web/dist' won't have a UI to mount.)" -ForegroundColor Yellow
    }
} else {
    Write-Host "    (no web/package.json found; skipping SPA build)" -ForegroundColor DarkGray
}

# --- Bootstrap .env (QUICKSTART section 0.4) --------------------------------
# Priority:
#   1. If repo already has .env, leave it alone.
#   2. Else if launch dir has a .env, copy it in (and it isn't the same file).
#   3. Else copy .env.example to .env so the user has a template to edit.
$targetEnv = Join-Path $repoDir '.env'
$exampleEnv = Join-Path $repoDir '.env.example'
if (Test-Path $targetEnv) {
    Write-Step ".env already present in repo - leaving it untouched"
} else {
    $launchEnv = Join-Path $LaunchDir '.env'
    $launchResolved = if (Test-Path $launchEnv) { (Resolve-Path $launchEnv).Path } else { $null }
    $targetResolvedDir = (Resolve-Path $repoDir).Path
    $launchResolvedDir = (Resolve-Path $LaunchDir).Path
    if ($launchResolved -and $launchResolvedDir -ne $targetResolvedDir) {
        Write-Step "Copying existing .env from launch directory into repo"
        Write-Host "    source : $launchEnv"
        Copy-Item -Path $launchEnv -Destination $targetEnv
    } elseif (Test-Path $exampleEnv) {
        Write-Step "Bootstrapping .env from .env.example (edit before running 'sma analyze-*')"
        Copy-Item -Path $exampleEnv -Destination $targetEnv
    } else {
        Write-Host "    (.env.example not found; skip .env bootstrap)" -ForegroundColor DarkGray
    }
}

# --- Verify CLI -------------------------------------------------------------
Write-Step "Verifying sma CLI"
sma --version
if ($LASTEXITCODE -ne 0) { throw "sma --version failed (exit $LASTEXITCODE)." }
sma --help | Select-Object -First 25

# --- Smoke test (offline doctor) --------------------------------------------
$doctorOk = $true
if (-not $SkipDoctor) {
    Write-Step "Running 'sma doctor --offline' smoke test"
    sma doctor --offline
    if ($LASTEXITCODE -ne 0) {
        $doctorOk = $false
        Write-Host "    (sma doctor --offline reported issues - review the output above.)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Next: see QUICKSTART.md section 0.3 (service principal) and section 0.4 (.env)." -ForegroundColor Green
Write-Host "        Edit '$targetEnv' before running 'sma analyze-*'."                              -ForegroundColor Green

# --- Launch the local control plane ----------------------------------------
# When everything is healthy, hand the user a running web UI on
# http://127.0.0.1:8000 instead of asking them to type one more command.
#
# On first launch we always send the browser to the Configuration tab so the
# user can fill in / review the .env-derived settings before kicking off a
# run. We pass --no-browser to `sma serve` (which would otherwise open the
# dashboard at /) and open /configuration ourselves from a background job
# once the port starts accepting connections.
$webDist = Join-Path $repoDir 'web\dist'
$serveHost = '127.0.0.1'
$servePort = 8000
$configUrl = "http://${serveHost}:${servePort}/configuration"

function Start-ConfigurationTabOpener {
    param(
        [Parameter(Mandatory)][string]$TargetHost,
        [Parameter(Mandatory)][int]$TargetPort,
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 30
    )
    # Background job that polls the loopback port and then launches the
    # default browser at the Configuration tab. Runs out-of-process so the
    # foreground `sma serve` invocation can keep owning the console.
    Start-Job -Name 'sma-open-configuration' -ScriptBlock {
        param($h, $p, $u, $timeout)
        $deadline = (Get-Date).AddSeconds($timeout)
        while ((Get-Date) -lt $deadline) {
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $iar = $client.BeginConnect($h, $p, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne(500) -and $client.Connected) {
                    $client.EndConnect($iar) | Out-Null
                    $client.Close()
                    Start-Sleep -Milliseconds 250
                    Start-Process $u | Out-Null
                    return
                }
                $client.Close()
            } catch {
                # Port not ready yet; keep polling.
            }
            Start-Sleep -Milliseconds 250
        }
    } -ArgumentList $TargetHost, $TargetPort, $Url, $TimeoutSeconds | Out-Null
}

if ($NoServe) {
    Write-Host ""
    Write-Host "Skipping 'sma serve' launch (-NoServe)." -ForegroundColor DarkGray
    Write-Host "Start it manually with: sma serve --with-api --static-dir web\dist" -ForegroundColor DarkGray
} elseif (-not $doctorOk) {
    Write-Host ""
    Write-Host "'sma doctor --offline' reported issues." -ForegroundColor Yellow
    Write-Host "Review the .env configuration." -ForegroundColor Yellow
    Write-Host ""
    Write-Step "Launching 'sma serve --with-api --static-dir web\dist' (Ctrl+C to stop)"
    Write-Host "    opening $configUrl in your browser." -ForegroundColor Green
    Start-ConfigurationTabOpener -TargetHost $serveHost -TargetPort $servePort -Url $configUrl
    sma serve --with-api --static-dir 'web\dist' --host $serveHost --port $servePort --no-browser
} elseif (-not (Test-Path (Join-Path $webDist 'index.html'))) {
    Write-Host ""
    Write-Host "Not launching 'sma serve' because '$webDist\index.html' is missing." -ForegroundColor Yellow
    Write-Host "Build the SPA first (cd web; npm install; npm run build), then run:" -ForegroundColor Yellow
    Write-Host "    sma serve --with-api --static-dir web\dist" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Step "Launching 'sma serve --with-api --static-dir web\dist' (Ctrl+C to stop)"
    Write-Host "    opening $configUrl in your browser." -ForegroundColor Green
    Start-ConfigurationTabOpener -TargetHost $serveHost -TargetPort $servePort -Url $configUrl
    sma serve --with-api --static-dir 'web\dist' --host $serveHost --port $servePort --no-browser
}
