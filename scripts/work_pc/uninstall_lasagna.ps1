[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$DesktopShortcutDir,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"

$DefaultInstallDir = Join-Path $env:LocalAppData "Programs\Lasagna"
$ResolvedInstallDir = if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $DefaultInstallDir
} else {
    [System.IO.Path]::GetFullPath($InstallDir)
}
$ResolvedDesktopDir = if ([string]::IsNullOrWhiteSpace($DesktopShortcutDir)) {
    [Environment]::GetFolderPath("DesktopDirectory")
} else {
    [System.IO.Path]::GetFullPath($DesktopShortcutDir)
}
$DefaultDataDir = Join-Path ([Environment]::GetFolderPath("DesktopDirectory")) "LasagnaRouteReviews"

function Test-LasagnaNoAdminPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $blockedRoots = @(
        [Environment]::GetFolderPath("ProgramFiles"),
        [Environment]::GetFolderPath("ProgramFilesX86"),
        $env:windir
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($root in $blockedRoots) {
        $blocked = [System.IO.Path]::GetFullPath($root)
        if ($full.StartsWith($blocked, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Lasagna cannot uninstall there without admin rights: $full"
        }
    }
}

function Remove-LasagnaFileIfPresent {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
}

Test-LasagnaNoAdminPath -Path $ResolvedInstallDir
Remove-LasagnaFileIfPresent -Path (Join-Path $ResolvedDesktopDir "Lasagna.lnk")

if (Test-Path -LiteralPath $ResolvedInstallDir) {
    Remove-Item -LiteralPath $ResolvedInstallDir -Recurse -Force
}

if ((-not $KeepData) -and (Test-Path -LiteralPath $DefaultDataDir)) {
    Remove-Item -LiteralPath $DefaultDataDir -Recurse -Force
}

Write-Host "Lasagna app removed: $ResolvedInstallDir"
