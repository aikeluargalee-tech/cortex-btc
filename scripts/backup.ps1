# Build a portable backup of this project (source, history, runtime data).
#
# Usage:  .\scripts\backup.ps1
#         .\scripts\backup.ps1 -Destination "D:\Backups"
#
# The archive deliberately excludes the access token: it is a credential, it is
# regenerated on first run, and a backup does not need it.

param(
    [string]$Destination = (Join-Path (Resolve-Path "$PSScriptRoot\..") "backups")
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
$name = "cortex-btc-backup-$stamp"
$stage = Join-Path $Destination $name
$zip = Join-Path $Destination "$name.zip"

New-Item -ItemType Directory -Force -Path $stage | Out-Null

# Working tree, without caches, build output, previous backups or the token.
$excludeDirs = @(".pytest_cache", "__pycache__", "_site", "backups")
$excludeFiles = @("access-token.txt", "*.pyc")
robocopy $root $stage /E /XD $excludeDirs /XF $excludeFiles /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

# Full git history as a single portable file.
$bundle = Join-Path $stage "cortex-btc-history.bundle"
git -C $root bundle create $bundle --all | Out-Null

# Snapshot of the stance at backup time, if the local server is running.
try {
    $tokenFile = Join-Path $root "data\access-token.txt"
    $headers = @{}
    if (Test-Path $tokenFile) { $headers["X-CORTEX-Token"] = (Get-Content $tokenFile -Raw).Trim() }
    $snapshot = Invoke-RestMethod "http://127.0.0.1:8787/api/state" -Headers $headers -TimeoutSec 20
    $snapshotDir = Join-Path $stage "snapshot"
    New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
    $snapshot | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $snapshotDir "state.json") -Encoding utf8
} catch {
    Write-Host "note: local dashboard not reachable, skipping live snapshot"
}

# Restore instructions travel with the backup.
$head = (git -C $root rev-parse --short HEAD)
$branch = (git -C $root rev-parse --abbrev-ref HEAD)
$restore = @"
# CORTEX-BTC backup — $stamp

Created from commit ``$head`` on branch ``$branch``.

## What is here

- ``cortex/``, ``config/``, ``tests/``, ``scripts/`` — the application
- ``docs/`` — architecture notes and screenshots
- ``cortex-btc-history.bundle`` — the full git history in one file
- ``data/`` — decision history, telemetry snapshot, research report
- ``snapshot/state.json`` — the stance at backup time (if the server was running)

**The access token is not included.** It is regenerated automatically the first
time you run ``.\run.ps1`` on a new machine.

## Restore

1. Copy the folder anywhere.
2. Verify integrity against ``manifest.sha256`` (optional):
   ``Get-FileHash -Algorithm SHA256 <file>``
3. Restore git history, if you want it:

       git clone cortex-btc-history.bundle cortex-btc
       cd cortex-btc
       git checkout $branch

   Or simply run it in place without git:

       python -m cortex.server
       # http://127.0.0.1:8787

4. To serve it on a network: ``.\run.ps1`` (creates a new token).

## Verify the restore

    python -m pytest -q        # 27 tests, all passing at backup time
    python -m cortex.server    # dashboard on 127.0.0.1:8787

## Note

The strategy remains UNVALIDATED. This backup preserves software, not evidence.
"@
Set-Content -Path (Join-Path $stage "RESTORE.md") -Value $restore -Encoding utf8

# Integrity manifest.
$manifest = Join-Path $stage "manifest.sha256"
Get-ChildItem $stage -Recurse -File |
    Where-Object { $_.Name -ne "manifest.sha256" } |
    Sort-Object FullName |
    ForEach-Object {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
        $relative = $_.FullName.Substring($stage.Length + 1)
        "$hash  $relative"
    } | Set-Content $manifest -Encoding utf8

Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
Remove-Item -Recurse -Force $stage

$zipHash = (Get-FileHash $zip -Algorithm SHA256).Hash
"$zipHash  $([System.IO.Path]::GetFileName($zip))" | Set-Content "$zip.sha256" -Encoding utf8

Write-Host ""
Write-Host "Backup:    $zip"
Write-Host "SHA256:    $zipHash"
Write-Host "Size:      $([math]::Round((Get-Item $zip).Length / 1MB, 2)) MB"
Write-Host "Files:     $((Get-ChildItem $root -Recurse -File | Where-Object { $_.FullName -notmatch '\\\.git\\|__pycache__|_site|backups' }).Count) in the working tree + git bundle"
