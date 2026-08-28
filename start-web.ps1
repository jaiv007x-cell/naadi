# Start NAADI web preview (http://localhost:8080)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WebRoot = Join-Path $PSScriptRoot "web"
if (-not (Test-Path (Join-Path $WebRoot "package.json"))) {
  Write-Error "Expected naadi/web/package.json — run from the naadi repo root."
}

Set-Location $WebRoot
if (-not (Test-Path "node_modules")) {
  Write-Host "Installing dependencies..."
  npm install
}

Write-Host ""
Write-Host "Starting NAADI Clinical OS at http://localhost:8080"
Write-Host "Case library: http://localhost:8080/cases"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

npm run dev
