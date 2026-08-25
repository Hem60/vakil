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
    [ValidateSet('help', 'install', 'data', 'fixtures', 'fit', 'run', 'run-without-proof', 'draft', 'draft-without-proof', 'extract-gemini', 'extract-claude', 'extract-stub', 'test', 'lint', 'eval', 'sweep', 'demo', 'verify', 'rules', 'up', 'down', 'clean')]
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
    <#
    .SYNOPSIS
      Run one build step.

    .PARAMETER Verdict
      Treat a non-zero exit as a finding rather than a crash.

      `verify` returns non-zero when the audit chain has been tampered with.
      That is the tool working, not the script breaking - but the default
      `throw` buried the one line that mattered under a PowerShell exception
      dump, which is the wrong thing to have on screen while demonstrating
      tamper detection to a panel. With -Verdict the exit code still
      propagates, so CI and shell pipelines behave exactly as before.
    #>
    param([string]$Description, [scriptblock]$Body, [switch]$Verdict)
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        if ($Verdict) { exit $LASTEXITCODE }
        throw "$Description failed (exit $LASTEXITCODE)"
    }
}

switch ($Target) {
    'help' {
        Write-Host ""
        Write-Host "  .\make.ps1 install   create .venv and install dependencies"
        Write-Host "  .\make.ps1 data      regenerate the synthetic corpus (-Seed $Seed -N $N)"
        Write-Host "  .\make.ps1 fixtures  render the proof-of-delivery documents"
        Write-Host "  .\make.ps1 fit       fit the win model on data/train"
        Write-Host "  .\make.ps1 extract-stub   score extraction, no key, no spend"
        Write-Host "  .\make.ps1 extract-gemini 20  score extraction, Gemini free tier"
        Write-Host "  .\make.ps1 extract-claude 20  score extraction, Claude"
        Write-Host "  .\make.ps1 test      run the unit tests"
        Write-Host "  .\make.ps1 eval      held-out evaluation      -> evals\report.md"
        Write-Host "  .\make.ps1 sweep     cost sensitivity sweep   -> evals\cost_sweep.md"
        Write-Host "  .\make.ps1 demo      assess one case, then verify the audit chain"
        Write-Host "  .\make.ps1 verify    walk the audit chain"
        Write-Host "  .\make.ps1 rules 13.1  what the networks require, with citations"
        Write-Host "  .\make.ps1 run       one dispute end to end, all eight stages"
        Write-Host "  .\make.ps1 run-without-proof   the same case, courier document withdrawn"
        Write-Host "  .\make.ps1 draft                 draft the rebuttal letter"
        Write-Host "  .\make.ps1 draft-without-proof   same case, courier document withdrawn"
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
    'fit'    { Invoke-Step 'fitting win model' { & $Py scripts\fit_win_model.py } }
    'extract-stub' { Invoke-Step 'scoring extraction (stub)' { & $Py evals\extraction_eval.py --backend stub } }
    'extract-gemini' { $n = if ($Arg) { $Arg } else { '0' }; Invoke-Step 'scoring extraction (gemini)' { & $Py evals\extraction_eval.py --backend gemini --limit $n } }
    'extract-claude' { $n = if ($Arg) { $Arg } else { '0' }; Invoke-Step 'scoring extraction (claude)' { & $Py evals\extraction_eval.py --backend claude --limit $n } }
    'test'   { Invoke-Step 'running tests' { & $Py -m pytest tests -q } }
    'eval'   { Invoke-Step 'held-out evaluation' { & $Py evals\run_eval.py } }
    'sweep'  { Invoke-Step 'cost sensitivity sweep' { & $Py evals\cost_sweep.py } }
    'verify' { Invoke-Step 'verifying audit chain' -Verdict { & $Py -m vakil.cli verify } }
    'rules'  { if ($Arg) { & $Py -m vakil.cli rules $Arg } else { & $Py -m vakil.cli rules } }
    'draft'  { $c = if ($Arg) { $Arg } else { 'data/test/case_0006.json' }; Invoke-Step 'drafting letter' { & $Py -m vakil.cli draft $c } }
    'run'    { $c = if ($Arg) { $Arg } else { 'data/test/case_0019.json' }; Invoke-Step 'running one dispute end to end' { & $Py -m vakil.cli run $c } }
    'run-without-proof' { $c = if ($Arg) { $Arg } else { 'data/test/case_0019.json' }; Invoke-Step 'end to end, courier document withdrawn' { & $Py -m vakil.cli run $c --drop delivery } }
    'draft-without-proof' { $c = if ($Arg) { $Arg } else { 'data/test/case_0006.json' }; Invoke-Step 'drafting without the courier document' { & $Py -m vakil.cli draft $c --drop delivery } }

    'demo' {
        if (Test-Path ledger.jsonl) { Remove-Item ledger.jsonl }
        $case = Get-ChildItem data\test\case_*.json -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $case) { throw "no cases found - run .\make.ps1 data first" }
        Invoke-Step "assessing $($case.Name)" { & $Py -m vakil.cli assess-case $case.FullName }
        Invoke-Step 'verifying audit chain' -Verdict { & $Py -m vakil.cli verify }
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
