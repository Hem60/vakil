<#
.SYNOPSIS
  Windows equivalent of the Makefile. `make` ships with Linux and macOS but not
  Windows, so this mirrors the same targets for PowerShell.

.EXAMPLE
  .\make.ps1 demo
  .\make.ps1 test
  .\make.ps1 data -Seed 20260824 -N 300
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'install', 'data', 'fixtures', 'test', 'lint', 'eval', 'sweep', 'demo', 'verify', 'rules', 'up', 'down', 'clean')]
    [string]$Target = 'help',

    [Parameter(Position = 1)]
    [string]$Arg,

    [int]$Seed = 20260824,
    [int]$N = 300
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# Prefer the project venv; fall back to whatever python is on PATH.
$Py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'

function Invoke-Step {
    param([string]$Description, [scriptblock]$Body)
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit $LASTEXITCODE)" }
}

switch ($Target) {
    'help' {
        Write-Host ""
        Write-Host "  .\make.ps1 install   create .venv and install dependencies"
        Write-Host "  .\make.ps1 data      regenerate the synthetic corpus (-Seed $Seed -N $N)"
        Write-Host "  .\make.ps1 fixtures  render the proof-of-delivery documents"
        Write-Host "  .\make.ps1 test      run the unit tests"
        Write-Host "  .\make.ps1 eval      held-out evaluation      -> evals\report.md"
        Write-Host "  .\make.ps1 sweep     cost sensitivity sweep   -> evals\cost_sweep.md"
        Write-Host "  .\make.ps1 demo      assess one case, then verify the audit chain"
        Write-Host "  .\make.ps1 verify    walk the audit chain"
        Write-Host "  .\make.ps1 rules 13.1  what the networks require, with citations"
        Write-Host "  .\make.ps1 up/down   start/stop postgres + api + web"
        Write-Host "  .\make.ps1 clean     remove caches and generated reports"
        Write-Host ""
    }

    'install' {
        Invoke-Step 'creating virtualenv' { python -m venv .venv }
        Invoke-Step 'upgrading pip' { & .venv\Scripts\python.exe -m pip install --upgrade pip }
        Invoke-Step 'installing dependencies' { & .venv\Scripts\python.exe -m pip install -e ".[dev]" }
    }

    'data'   { Invoke-Step 'generating corpus' { & $Py data\generator\generate.py --seed $Seed --n $N } }
    'fixtures' { Invoke-Step 'rendering POD documents' { & $Py data\generator\fixtures.py --seed $Seed } }
    'test'   { Invoke-Step 'running tests' { & $Py -m pytest tests -q } }
    'eval'   { Invoke-Step 'held-out evaluation' { & $Py evals\run_eval.py } }
    'sweep'  { Invoke-Step 'cost sensitivity sweep' { & $Py evals\cost_sweep.py } }
    'verify' { Invoke-Step 'verifying audit chain' { & $Py -m vakil.cli verify } }
    'rules'  { if ($Arg) { & $Py -m vakil.cli rules $Arg } else { & $Py -m vakil.cli rules } }

    'demo' {
        if (Test-Path ledger.jsonl) { Remove-Item ledger.jsonl }
        $case = Get-ChildItem data\test\case_*.json -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $case) { throw "no cases found - run .\make.ps1 data first" }
        Invoke-Step "assessing $($case.Name)" { & $Py -m vakil.cli assess-case $case.FullName }
        Invoke-Step 'verifying audit chain' { & $Py -m vakil.cli verify }
    }

    'lint' {
        Invoke-Step 'ruff' { & $Py -m ruff check src tests evals data }
        Invoke-Step 'mypy' { & $Py -m mypy }
    }

    'up'   { Invoke-Step 'starting services' { docker compose up -d } }
    'down' { Invoke-Step 'stopping services' { docker compose down } }

    'clean' {
        foreach ($p in '.pytest_cache', '.ruff_cache', '.mypy_cache', 'ledger.jsonl',
                       'evals\report.md', 'evals\report.json',
                       'evals\cost_sweep.md', 'evals\cost_sweep.json') {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
        Write-Host "cleaned" -ForegroundColor Cyan
    }
}
