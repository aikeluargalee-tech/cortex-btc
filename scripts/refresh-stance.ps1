# Dispatch a Pages rebuild and wait for it to finish.
#
# GitHub's own cron is best-effort: `*/15` can be skipped for hours on a
# low-traffic repository. This script triggers the workflow explicitly, which is
# deterministic, and logs the outcome.
#
# Run it by hand, or let the "CORTEX BTC stance publish" scheduled task call it
# every 10 minutes while this laptop is on.

param(
    [switch]$Wait,
    [string]$Workflow = "publish-stance.yml",
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$log = Join-Path $root "data\publish.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-Log([string]$message) {
    "$((Get-Date).ToString('s'))  $message" | Add-Content -Path $log -Encoding utf8
}

# Scheduled tasks do not always inherit the interactive PATH.
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) {
    foreach ($candidate in @("$env:ProgramFiles\GitHub CLI\gh.exe", "$env:LOCALAPPDATA\GitHubCLI\gh.exe")) {
        if (Test-Path $candidate) { $gh = $candidate; break }
    }
}
if (-not $gh) {
    Write-Log "ERROR: gh CLI not found"
    exit 1
}

try {
    Set-Location $root
    $previous = & $gh run list --workflow=$Workflow --limit 1 --json databaseId --jq '.[0].databaseId' 2>$null
    & $gh workflow run $Workflow --ref $Ref | Out-Null
    Write-Log "dispatched $Workflow on $Ref"

    if (-not $Wait) { exit 0 }

    Start-Sleep -Seconds 6
    $id = & $gh run list --workflow=$Workflow --limit 1 --json databaseId --jq '.[0].databaseId'
    if (-not $id -or $id -eq $previous) {
        Write-Log "WARN: no new run detected"
        exit 0
    }
    & $gh run watch $id --exit-status --interval 5 *> $null
    $conclusion = & $gh run view $id --json conclusion --jq '.conclusion'
    Write-Log "run $id finished: $conclusion"
    if ($conclusion -ne "success") { exit 1 }
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
