[CmdletBinding()]
param(
    [string]$PackageDir,
    [string]$ZipPath
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Join-Path $Root "dist\Lasagna"
}
if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $ZipPath = Join-Path $Root "dist\Lasagna.zip"
}

$PackageDir = [System.IO.Path]::GetFullPath($PackageDir)
$ZipPath = [System.IO.Path]::GetFullPath($ZipPath)
$AppDir = Join-Path $PackageDir "app"
$AuditScript = Join-Path $PSScriptRoot "audit_lasagna_package.ps1"

function Copy-LasagnaRequiredItem {
    param(
        [string]$RelativePath,
        [string]$DestinationRoot
    )
    $source = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required package item missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination $DestinationRoot -Recurse -Force
}

if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PackageDir, $AppDir | Out-Null

Copy-LasagnaRequiredItem -RelativePath "src" -DestinationRoot $AppDir
Copy-LasagnaRequiredItem -RelativePath "assets" -DestinationRoot $AppDir
Copy-LasagnaRequiredItem -RelativePath "scripts" -DestinationRoot $AppDir
Copy-LasagnaRequiredItem -RelativePath "requirements-runtime.txt" -DestinationRoot $AppDir
Copy-LasagnaRequiredItem -RelativePath "pyproject.toml" -DestinationRoot $AppDir

[System.IO.File]::WriteAllText(
    (Join-Path $PackageDir "INSTALL_LASAGNA.cmd"),
    "@echo off`r`nset `"SCRIPT_DIR=%~dp0`"`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%SCRIPT_DIR%app\scripts\work_pc\install_lasagna.ps1`" %*`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

Get-ChildItem -LiteralPath $PackageDir -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $PackageDir -File -Filter "*.pyc" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Force
[System.IO.File]::WriteAllText(
    (Join-Path $PackageDir "UNINSTALL_LASAGNA.cmd"),
    "@echo off`r`nset `"SCRIPT_DIR=%~dp0`"`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%SCRIPT_DIR%app\scripts\work_pc\uninstall_lasagna.ps1`" %*`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

$startHere = @"
Lasagna

1. Extract this folder.
2. Double-click INSTALL_LASAGNA.cmd.
3. Keep the recommended install location.
4. Click the Lasagna desktop icon.
5. Paste IC/ICB IDs and generate route workbooks.

Generated workbooks are written outside the app folder.
"@
[System.IO.File]::WriteAllText((Join-Path $PackageDir "START_HERE.txt"), $startHere, [System.Text.UTF8Encoding]::new($false))

$manifest = [pscustomobject][ordered]@{
    package = "Lasagna"
    admin_required = $false
    path_mutation = $false
    desktop_shortcut = "Lasagna.lnk"
    icon = "app/assets/brand/lasagna.ico"
}
[System.IO.File]::WriteAllText((Join-Path $PackageDir "package-manifest.json"), ($manifest | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ZipPath) | Out-Null
Compress-Archive -Path $PackageDir -DestinationPath $ZipPath -Force

$inventoryPath = Join-Path (Split-Path -Parent $ZipPath) "lasagna-package-inventory.json"
$summaryPath = Join-Path (Split-Path -Parent $ZipPath) "lasagna-package-audit-summary.json"
& powershell -NoProfile -ExecutionPolicy Bypass -File $AuditScript -ZipPath $ZipPath -InventoryPath $inventoryPath -SummaryPath $summaryPath
if ($LASTEXITCODE -ne 0) {
    throw "Lasagna package audit failed with exit code $LASTEXITCODE."
}

Write-Host "Lasagna package folder: $PackageDir"
Write-Host "Lasagna package zip: $ZipPath"
