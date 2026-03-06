# PolyEdge deployment to 256GB Windows Server (88.99.142.89)
# Run from Administrator PowerShell on the target server.
#
# Prerequisites:
#   - Python 3.12+ installed and on PATH
#   - Git installed
#   - PostgreSQL accessible at 89.167.99.187:5432
#
# Usage:
#   .\deploy\deploy_256gb.ps1

$ErrorActionPreference = "Stop"
$PROJECT_DIR = "C:\polyedge"
$REPO_URL   = "https://github.com/your-org/supergod.git"
$DB_HOST     = "89.167.99.187"

Write-Host "=== PolyEdge Deployment to 256GB Server ===" -ForegroundColor Cyan

# ---- 1. Sync code ----
if (Test-Path "$PROJECT_DIR\.git") {
    Write-Host "[1/6] Pulling latest code..."
    Push-Location $PROJECT_DIR
    git pull --ff-only
    Pop-Location
} else {
    Write-Host "[1/6] Cloning repository..."
    git clone $REPO_URL $PROJECT_DIR
}

# ---- 2. Create/update venv ----
Write-Host "[2/6] Setting up Python virtual environment..."
Push-Location "$PROJECT_DIR\polyedge"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

# Activate and install
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[dev]"

Write-Host "[3/6] Verifying key packages..."
python -c "import scipy; print(f'scipy {scipy.__version__}')"
python -c "import sklearn; print(f'scikit-learn {sklearn.__version__}')"
python -c "import yfinance; print(f'yfinance {yfinance.__version__}')"
python -c "import requests; print(f'requests {requests.__version__}')"

# ---- 3. Run DB migrations on PostgreSQL server ----
Write-Host "[4/6] Running DB migrations on $DB_HOST..."
$env:POLYEDGE_DATABASE_URL = "postgresql+asyncpg://polyedge:polyedge@${DB_HOST}:5432/polyedge"

# Run the v3 migration SQL chain directly via Python.
python -c @"
import asyncio, asyncpg, pathlib

async def run():
    conn = await asyncpg.connect('postgresql://polyedge:polyedge@$DB_HOST:5432/polyedge')
    migrations = [
        'deploy/migrations/001_v3_tables.sql',
        'deploy/migrations/002_market_resolution_source.sql',
        'deploy/migrations/003_service_heartbeats.sql',
        'deploy/migrations/004_trading_rule_tier.sql',
    ]
    for path in migrations:
        print(f'  Applying {path}...')
        sql = pathlib.Path(path).read_text()
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    print(f'    (skip: {e})')
    await conn.close()
    print('  Migrations applied.')

asyncio.run(run())
"@

# ---- 4. Create .env if missing ----
if (-not (Test-Path ".env")) {
    Write-Host "[5/6] Creating .env template..."
    @"
# PolyEdge Environment Variables
POLYEDGE_DATABASE_URL=postgresql+asyncpg://polyedge:polyedge@${DB_HOST}:5432/polyedge

# API Keys (add as you acquire them)
# FRED_API_KEY=
# ALPHA_VANTAGE_API_KEY=
# FINNHUB_API_KEY=
# NEWSAPI_API_KEY=
# OPENWEATHERMAP_API_KEY=
# POLYGON_API_KEY=
# FMP_API_KEY=
# TWELVE_DATA_API_KEY=
# EIA_API_KEY=
# COINMARKETCAP_API_KEY=
# WEATHERBIT_API_KEY=
# NASA_API_KEY=
# TMDB_API_KEY=
# GUARDIAN_API_KEY=
# OPENAQ_API_KEY=
# SHODAN_API_KEY=
# GOOGLE_TRENDS_API_KEY=
"@ | Out-File -FilePath ".env" -Encoding utf8
    Write-Host "  .env created — fill in your API keys."
} else {
    Write-Host "[5/6] .env already exists, skipping."
}

# ---- 5. Set up Windows Scheduled Task ----
Write-Host "[6/6] Setting up scheduler task..."

$pythonExe = (Resolve-Path ".venv\Scripts\python.exe").Path
$hostName = $env:COMPUTERNAME
$runScriptPath = "$PROJECT_DIR\polyedge\deploy\run_scheduler_windows.ps1"

@"
`$ErrorActionPreference = 'Stop'
`$env:POLYEDGE_DATABASE_URL = 'postgresql+asyncpg://polyedge:polyedge@${DB_HOST}:5432/polyedge'
`$env:POLYEDGE_SCHEDULER_HOST = '$hostName'
Set-Location '$PROJECT_DIR\polyedge'
`$envFile = '.env'
if (Test-Path `$envFile) {
    Get-Content `$envFile | ForEach-Object {
        `$line = (`$_).Trim()
        if (-not `$line -or `$line.StartsWith('#')) { return }
        `$parts = `$line.Split('=', 2)
        if (`$parts.Length -eq 2) {
            Set-Item -Path ("Env:" + `$parts[0].Trim()) -Value `$parts[1].Trim()
        }
    }
}
while (`$true) {
    try {
        & '$pythonExe' -m polyedge.cli run
    } catch {
        Start-Sleep -Seconds 5
    }
}
"@ | Out-File -FilePath $runScriptPath -Encoding utf8

$taskName = "PolyEdge-Scheduler"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScriptPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "PolyEdge unified scheduler loop" -User "SYSTEM" -RunLevel Highest
Start-ScheduledTask -TaskName $taskName
Write-Host "  Registered: $taskName (startup + auto-restart loop)"

Pop-Location

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Fill in API keys in $PROJECT_DIR\polyedge\.env"
Write-Host "  2. Run initial backfill: $PROJECT_DIR\polyedge\deploy\backfill.bat"
Write-Host "  3. Verify dashboard at http://${DB_HOST}:8090"
Write-Host "  4. Confirm scheduler task: Get-ScheduledTask -TaskName PolyEdge-Scheduler"
