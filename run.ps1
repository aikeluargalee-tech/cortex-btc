# Start the CORTEX-BTC dashboard for the local network.
# Bind: all interfaces. Access: token required (stored in data/access-token.txt).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$tokenFile = Join-Path $root "data\access-token.txt"
if (-not (Test-Path $tokenFile)) {
    $token = (python -c "import secrets; print(secrets.token_hex(9))").Trim()
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tokenFile) | Out-Null
    Set-Content -Path $tokenFile -Value $token -Encoding ascii -NoNewline
}

$env:CORTEX_HOST = "0.0.0.0"
$env:CORTEX_PORT = "8787"
$env:CORTEX_TOKEN = (Get-Content $tokenFile -Raw).Trim()
$env:PYTHONIOENCODING = "utf-8"

Write-Host "Token file: $tokenFile"
Write-Host "Open from another device: http://<this-laptop-ip>:8787/?token=<token>"
python -m cortex.server
