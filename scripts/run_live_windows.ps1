$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Environment not found. Run: py -3.12 -m venv .venv"
}

& $python -m arbitrage_poly.apps.live_cli `
    --min-net-edge 0.01 `
    --slippage-buffer 0.01 `
    --fixed-stake-usd 10 `
    --refresh-interval-s 1