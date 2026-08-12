$ErrorActionPreference = "Stop"

$recordingProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$recordingExecutable = Join-Path $recordingProjectRoot ".venv\Scripts\glassbox.exe"
$recordingDataDir = Join-Path $recordingProjectRoot ".glassbox\recordings"
$recordingTimestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$recordingDatabase = Join-Path $recordingDataDir "recording-$recordingTimestamp.db"

if (-not (Test-Path -LiteralPath $recordingExecutable)) {
    throw "Virtual environment not found. Run the README installation steps first."
}

New-Item -ItemType Directory -Path $recordingDataDir -Force | Out-Null

$recordingPreviousBudget = [Environment]::GetEnvironmentVariable(
    "GLASSBOX_CONTEXT_CHAR_BUDGET",
    "Process"
)
$recordingPreviousRecentTurns = [Environment]::GetEnvironmentVariable(
    "GLASSBOX_RECENT_TURNS",
    "Process"
)
$recordingPreviousFailure = [Environment]::GetEnvironmentVariable(
    "GLASSBOX_FAIL_ONCE_TOOL",
    "Process"
)

try {
    $env:GLASSBOX_CONTEXT_CHAR_BUDGET = "2200"
    $env:GLASSBOX_RECENT_TURNS = "1"
    $env:GLASSBOX_FAIL_ONCE_TOOL = "search_docs"

    Write-Host "GlassBox recording rehearsal"
    Write-Host "Fresh database: $recordingDatabase"
    Write-Host "Demo controls: 2200-char budget, 1 recent turn, search_docs fails once"
    Write-Host "No API key value is printed by this script."

    & $recordingExecutable doctor
    if ($LASTEXITCODE -ne 0) {
        throw "Doctor failed. Stop the recording and fix the configuration before retrying."
    }

    & $recordingExecutable chat --db $recordingDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "GlassBox chat exited with code $LASTEXITCODE."
    }
}
finally {
    if ($null -eq $recordingPreviousBudget) {
        Remove-Item Env:GLASSBOX_CONTEXT_CHAR_BUDGET -ErrorAction SilentlyContinue
    }
    else {
        $env:GLASSBOX_CONTEXT_CHAR_BUDGET = $recordingPreviousBudget
    }

    if ($null -eq $recordingPreviousRecentTurns) {
        Remove-Item Env:GLASSBOX_RECENT_TURNS -ErrorAction SilentlyContinue
    }
    else {
        $env:GLASSBOX_RECENT_TURNS = $recordingPreviousRecentTurns
    }

    if ($null -eq $recordingPreviousFailure) {
        Remove-Item Env:GLASSBOX_FAIL_ONCE_TOOL -ErrorAction SilentlyContinue
    }
    else {
        $env:GLASSBOX_FAIL_ONCE_TOOL = $recordingPreviousFailure
    }
}
