param(
  [string]$RepoDir = "C:\supergod",
  [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "$RepoDir\.git")) {
  Write-Error "supergod-sync: $RepoDir is not a git repo"
}

Push-Location $RepoDir
try {
  git fetch origin $Branch | Out-Null
  $localRev = (git rev-parse HEAD).Trim()
  $remoteRev = (git rev-parse "origin/$Branch").Trim()

  if ($localRev -eq $remoteRev) {
    Write-Output "supergod-sync: no updates ($localRev)"
    exit 0
  }

  Write-Output "supergod-sync: updating $localRev -> $remoteRev"
  git reset --hard "origin/$Branch" | Out-Null
  python -m pip install -e $RepoDir | Out-Null

  $tasks = @("SupergodOrch", "SupergodWorker", "SupergodWorker2", "SupergodWorker3")
  foreach ($t in $tasks) {
    schtasks /run /tn $t | Out-Null
  }
  Write-Output "supergod-sync: update complete"
} finally {
  Pop-Location
}
