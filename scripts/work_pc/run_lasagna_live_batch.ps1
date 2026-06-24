[CmdletBinding()]
param(
    [string[]]$ServiceId = @(),
    [string]$IdsFile,
    [string]$IdsText,
    [string]$OutputDir,
    [string]$Connection = "sdm_runner",
    [int]$MaxServiceTabs = 100,
    [switch]$KeepCombinedCsv
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SourcePath = Join-Path $Root "src"
if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "Lasagna source folder not found: $SourcePath"
}

$env:PYTHONPATH = $SourcePath
$argsList = @("-m", "lasagna.live_batch", "--connection", $Connection, "--max-service-tabs", [string]$MaxServiceTabs)
foreach ($id in $ServiceId) {
    $argsList += @("--service-id", $id)
}
if (-not [string]::IsNullOrWhiteSpace($IdsFile)) {
    $argsList += @("--ids-file", ([System.IO.Path]::GetFullPath($IdsFile)))
}
if (-not [string]::IsNullOrWhiteSpace($IdsText)) {
    $argsList += @("--ids-text", $IdsText)
}
if (-not [string]::IsNullOrWhiteSpace($OutputDir)) {
    $argsList += @("--output-dir", ([System.IO.Path]::GetFullPath($OutputDir)))
}
if ($KeepCombinedCsv) {
    $argsList += "--keep-combined-csv"
}

python @argsList
