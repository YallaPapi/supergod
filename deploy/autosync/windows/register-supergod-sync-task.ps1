param(
  [string]$TaskName = "SupergodSync",
  [string]$RepoDir = "C:\supergod",
  [int]$IntervalMinutes = 1
)

$ErrorActionPreference = "Stop"

$scriptPath = "$RepoDir\deploy\autosync\windows\supergod-sync.ps1"
if (-not (Test-Path $scriptPath)) {
  Write-Error "Missing script: $scriptPath"
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -RepoDir `"$RepoDir`""
schtasks /create /f /sc minute /mo $IntervalMinutes /tn $TaskName /tr $action /ru SYSTEM | Out-Null
Write-Output "Registered task '$TaskName' ($IntervalMinutes minute interval)"
