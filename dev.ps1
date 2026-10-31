# FansCut local dev helper (Windows / PowerShell)
#
#   .\dev.ps1 setup          install Python and UI dependencies
#   .\dev.ps1 serve          run the app (http://127.0.0.1:6464)
#   .\dev.ps1 ui             UI dev server with live reload (http://127.0.0.1:5464)
#   .\dev.ps1 test           pytest, in parallel
#   .\dev.ps1 lint           ruff + mypy
#   .\dev.ps1 validate       everything CI runs: ruff, mypy, pytest, UI lint, UI tests, UI build
#   .\dev.ps1 cli <args...>  pass through to the fanscut CLI
#
# Set FANSCUT_VENV to use a venv outside the repo.

param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = "python"
if ($env:FANSCUT_VENV -and (Test-Path "$env:FANSCUT_VENV\Scripts\python.exe")) {
    $Python = "$env:FANSCUT_VENV\Scripts\python.exe"
} elseif (Test-Path ".venv\Scripts\python.exe") {
    $Python = ".venv\Scripts\python.exe"
}

function Step([string]$Name, [scriptblock]$Block) {
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Ui([scriptblock]$Block) {
    Push-Location ui
    try { & $Block } finally { Pop-Location }
}

switch ($Command) {
    "setup" {
        Step "pip install -e .[dev]" { & $Python -m pip install -e ".[dev]" }
        Step "npm ci" { Ui { npm ci } }
    }
    "serve" { & $Python -m fanscut serve @Rest; exit $LASTEXITCODE }
    "ui" {
        if (-not (Test-Path "ui\node_modules")) { Step "npm ci" { Ui { npm ci } } }
        Ui { npm run dev -- @Rest }
    }
    "test" { & $Python -m pytest -q -n auto @Rest; exit $LASTEXITCODE }
    "lint" {
        Step "ruff" { & $Python -m ruff check . }
        Step "mypy" { & $Python -m mypy fanscut/ }
    }
    "validate" {
        Step "ruff" { & $Python -m ruff check . }
        Step "mypy" { & $Python -m mypy fanscut/ }
        Step "pytest" { & $Python -m pytest -q -n auto }
        if (-not (Test-Path "ui\node_modules")) { Step "npm ci" { Ui { npm ci } } }
        Step "ui lint" { Ui { npm run lint } }
        Step "ui test" { Ui { npm test } }
        Step "ui build" { Ui { npm run build } }
        Write-Host "`nall good" -ForegroundColor Green
    }
    "cli" { & $Python -m fanscut @Rest; exit $LASTEXITCODE }
    default { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 11 }
}
