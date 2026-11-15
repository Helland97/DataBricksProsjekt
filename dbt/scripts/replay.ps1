<#
.SYNOPSIS
    Replays simulated days: load bronze as of day N, then snapshot it as of day N.
    Finishes with a full dbt build on the last day.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File dbt\scripts\replay.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File dbt\scripts\replay.ps1 -Days (1..30)
#>
param(
    [int[]]$Days = @(1, 5, 9, 10, 15, 20, 30),
    [datetime]$StartDate = '2026-10-01'
)

$repo      = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$ingestion = Join-Path $repo 'ingestion'
$dbt       = Join-Path $repo 'dbt'

function Invoke-Step([string]$What, [scriptblock]$Cmd) {
    Write-Host "`n>>> $What" -ForegroundColor Cyan
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: $What (exit code $LASTEXITCODE)" }
}

Invoke-Step 'reset snapshots (history starts over together with bronze)' {
    uv run --directory $dbt dbt run-operation reset_snapshots
}

$first = $true
foreach ($d in $Days) {
    $asOf    = $StartDate.AddDays($d - 1).ToString('yyyy-MM-dd')
    # Build the argument list as an explicit array. (An `if` that returns @('--refresh') is unrolled
    # to a plain string, and splatting a string passes it one character at a time: "- - r e f r e s h".)
    $loadArgs = @('run', '--directory', $ingestion, 'python', '-m', 'pipelines.regnskap',
                  '--day', $d, '--destination', 'databricks')
    if ($first) { $loadArgs += '--refresh' }

    Invoke-Step "day $d ($asOf): load bronze" {
        uv @loadArgs
    }
    Invoke-Step "day $d ($asOf): snapshot" {
        uv run --directory $dbt dbt snapshot --vars "{snapshot_time: '$asOf'}"
    }
    $first = $false
}

Invoke-Step "full build as of $asOf" {
    uv run --directory $dbt dbt build --full-refresh --vars "{snapshot_time: '$asOf'}"
}
