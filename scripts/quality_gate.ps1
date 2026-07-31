param(
    [switch]$SkipEvaluations
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DataAgent = Join-Path $ProjectRoot ".venv\Scripts\data-agent.exe"

if (-not (Test-Path $Python) -or -not (Test-Path $DataAgent)) {
    throw 'Project virtual environment is missing. Install with: python -m pip install -e ".[dev]"'
}

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Command
    )

    Write-Host "`n== $Label ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Invoke-AgentEvaluation {
    param(
        [string]$Dataset,
        [string]$Label
    )

    Write-Host "`n== $Label ==" -ForegroundColor Cyan
    $arguments = @("eval", "--mode", "offline")
    if ($Dataset) {
        $arguments += @("--dataset", $Dataset)
    }
    $raw = & $DataAgent @arguments | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
    $report = $raw | ConvertFrom-Json
    Write-Host (
        "{0}/{1} passed; intent={2:P0}; entity={3:P0}; tool={4:P0}; agent={5:P0}; answer={6:P0}; citation={7:P0}" -f
        $report.passed,
        $report.total,
        $report.intent_accuracy,
        $report.entity_accuracy,
        $report.tool_accuracy,
        $report.agent_accuracy,
        $report.answer_accuracy,
        $report.citation_accuracy
    )
}

Push-Location $ProjectRoot
try {
    Invoke-Checked "Ruff" { & $Python -m ruff check src app.py tests }
    Invoke-Checked "Mypy core" { & $Python -m mypy }
    Invoke-Checked "Pytest" { & $Python -m pytest tests }

    if (-not $SkipEvaluations) {
        Invoke-AgentEvaluation "" "Core Agent evaluation"
        Invoke-AgentEvaluation "synthetic_understanding.json" "Synthetic silver evaluation"
        Invoke-AgentEvaluation "nlu_questions.json" "NLU provider evaluation"
    }

    Invoke-Checked "Dependency check" { & $Python -m pip check }
    Write-Host "`nQuality gate passed." -ForegroundColor Green
}
finally {
    Pop-Location
}