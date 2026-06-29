[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$DesktopShortcutDir,
    [switch]$NoDesktopShortcut,
    [switch]$NoLaunch,
    [switch]$PlanOnly,
    [switch]$ValidateNoAdmin
)

$ErrorActionPreference = "Stop"

$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DefaultInstallDir = Join-Path $env:LocalAppData "Programs\Lasagna"

function Resolve-LasagnaInstallDir {
    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        return $DefaultInstallDir
    }
    return [System.IO.Path]::GetFullPath($InstallDir)
}

function Resolve-LasagnaDesktopDir {
    if ([string]::IsNullOrWhiteSpace($DesktopShortcutDir)) {
        return [Environment]::GetFolderPath("DesktopDirectory")
    }
    return [System.IO.Path]::GetFullPath($DesktopShortcutDir)
}

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
            throw "Lasagna cannot install there without admin rights: $full"
        }
    }
}

function Copy-LasagnaTree {
    param([string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in @("src", "assets", "scripts", "requirements-runtime.txt", "pyproject.toml")) {
        $source = Join-Path $SourceRoot $item
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Required Lasagna package item missing: $source"
        }
        Copy-Item -LiteralPath $source -Destination $Destination -Recurse -Force
    }
}

function New-LasagnaShortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetScript,
        [string]$WorkingDirectory,
        [string]$IconPath
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$TargetScript`""
    $shortcut.WorkingDirectory = $WorkingDirectory
    if (Test-Path -LiteralPath $IconPath) {
        $shortcut.IconLocation = $IconPath
    }
    $shortcut.Save()
}

$ResolvedInstallDir = Resolve-LasagnaInstallDir
$ResolvedDesktopDir = Resolve-LasagnaDesktopDir
$LauncherPath = Join-Path $ResolvedInstallDir "scripts\work_pc\run_lasagna.ps1"
$IconPath = Join-Path $ResolvedInstallDir "assets\brand\lasagna.ico"
$ShortcutPath = Join-Path $ResolvedDesktopDir "Lasagna.lnk"

Test-LasagnaNoAdminPath -Path $ResolvedInstallDir
if ($ValidateNoAdmin) {
    Test-LasagnaNoAdminPath -Path $ResolvedDesktopDir
}

$plan = [pscustomobject][ordered]@{
    install_dir = $ResolvedInstallDir
    desktop_shortcut = $(if ($NoDesktopShortcut) { $null } else { $ShortcutPath })
    icon_path = $IconPath
    admin_required = $false
    path_mutation = $false
    source_root = $SourceRoot
}

if ($PlanOnly) {
    $plan | ConvertTo-Json -Depth 4
    return
}

Copy-LasagnaTree -Destination $ResolvedInstallDir
if (-not $NoDesktopShortcut) {
    New-Item -ItemType Directory -Force -Path $ResolvedDesktopDir | Out-Null
    New-LasagnaShortcut `
        -ShortcutPath $ShortcutPath `
        -TargetScript $LauncherPath `
        -WorkingDirectory $ResolvedInstallDir `
        -IconPath $IconPath
}

$manifest = [pscustomobject][ordered]@{
    install_dir = $ResolvedInstallDir
    desktop_shortcut = $(if ($NoDesktopShortcut) { $null } else { $ShortcutPath })
    icon_path = $IconPath
    admin_required = $false
    path_mutation = $false
}
$manifestPath = Join-Path $ResolvedInstallDir "lasagna-install-manifest.json"
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))

Write-Host "Lasagna installed: $ResolvedInstallDir"
if (-not $NoDesktopShortcut) {
    Write-Host "Lasagna desktop shortcut: $ShortcutPath"
}
if (-not $NoLaunch -and -not $NoDesktopShortcut) {
    Start-Process -FilePath $ShortcutPath
}
