[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SourcePath = Join-Path $Root "src"

if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "Lasagna source folder not found: $SourcePath"
}

$env:PYTHONPATH = $SourcePath
if ($ValidateOnly) {
    python -c "import lasagna.ui.app; print('LASAGNA_LAUNCHER_VALIDATE=PASS')"
    return
}
python -m lasagna.ui.app
