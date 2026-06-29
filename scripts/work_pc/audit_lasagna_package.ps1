[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,
    [string]$InventoryPath,
    [string]$SummaryPath
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InventoryPath)) {
    $InventoryPath = Join-Path (Split-Path -Parent $ZipPath) "lasagna-package-inventory.json"
}
if ([string]::IsNullOrWhiteSpace($SummaryPath)) {
    $SummaryPath = Join-Path (Split-Path -Parent $ZipPath) "lasagna-package-audit-summary.json"
}
if (-not (Test-Path -LiteralPath $ZipPath)) {
    throw "Missing package zip: $ZipPath"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

function ConvertTo-LasagnaArtifactPath {
    param([string]$ZipEntryName)
    $normalized = $ZipEntryName.Replace("\", "/")
    if ($normalized.StartsWith("Lasagna/")) {
        return $normalized.Substring("Lasagna/".Length)
    }
    return $normalized
}

function Test-LasagnaTextEntry {
    param([string]$RelativePath)
    $extension = [System.IO.Path]::GetExtension($RelativePath).ToLowerInvariant()
    return @(".cmd", ".ps1", ".py", ".txt", ".json", ".sql", ".md", ".toml", ".yml", ".yaml") -contains $extension
}

function Get-LasagnaForbiddenReasons {
    param(
        [System.IO.Compression.ZipArchiveEntry]$ZipEntry,
        [string]$RelativePath
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $lower = $RelativePath.ToLowerInvariant()
    if ($lower.StartsWith(".git/") -or $lower.Contains("/.git/")) { $reasons.Add("git_present") }
    if ($lower.StartsWith("tests/") -or $lower.Contains("/tests/")) { $reasons.Add("tests_present") }
    if ($lower.StartsWith("docs/") -or $lower.Contains("/docs/")) { $reasons.Add("docs_present") }
    if ($lower.StartsWith("build/") -or $lower.StartsWith("dist/")) { $reasons.Add("build_output_present") }
    if ($lower.Contains("__pycache__/") -or $lower.EndsWith(".pyc")) { $reasons.Add("python_cache_present") }
    if ($lower.EndsWith(".xlsx") -or $lower.EndsWith(".xlsm") -or $lower.EndsWith(".xls")) { $reasons.Add("workbook_present") }
    if ($lower.EndsWith(".csv") -or $lower.EndsWith(".log") -or $lower.EndsWith(".jsonl")) { $reasons.Add("raw_or_log_artifact_present") }
    if ($lower.Contains(".snowflake") -or $lower.Contains("snowsql")) { $reasons.Add("snowflake_config_present") }

    if (Test-LasagnaTextEntry -RelativePath $RelativePath) {
        $stream = $null
        $reader = $null
        try {
            $stream = $ZipEntry.Open()
            $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false, $true), $true)
            $text = $reader.ReadToEnd()
            if ($text -match "(?i)(token|password|secret|api[_-]?key)\s*=\s*['`"][^'`"]{8,}['`"]") {
                $reasons.Add("secret_literal_present")
            }
            if ($text -match "(?i)c:[\\/]+users[\\/]") {
                $reasons.Add("local_user_path_present")
            }
        }
        catch {
            $reasons.Add("unreadable_text_entry")
        }
        finally {
            if ($null -ne $reader) {
                $reader.Dispose()
            }
            elseif ($null -ne $stream) {
                $stream.Dispose()
            }
        }
    }

    return @($reasons)
}

$zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    $inventory = @()
    foreach ($entry in @($zip.Entries | Where-Object { -not [string]::IsNullOrWhiteSpace($_.Name) })) {
        $relativePath = ConvertTo-LasagnaArtifactPath -ZipEntryName $entry.FullName
        $forbiddenReasons = @(Get-LasagnaForbiddenReasons -ZipEntry $entry -RelativePath $relativePath)
        $inventory += [pscustomobject][ordered]@{
            zip_path = $entry.FullName
            artifact_path = $relativePath
            bytes = [int64]$entry.Length
            forbidden_reasons = $forbiddenReasons
        }
    }
}
finally {
    $zip.Dispose()
}

$forbidden = @($inventory | Where-Object { @($_.forbidden_reasons).Count -gt 0 })
$summary = [pscustomobject][ordered]@{
    status = $(if ($forbidden.Count -eq 0) { "success" } else { "failed" })
    zip_path = [System.IO.Path]::GetFullPath($ZipPath)
    zip_file_count = $inventory.Count
    zip_total_bytes = [int64](($inventory | Measure-Object -Property bytes -Sum).Sum)
    forbidden_file_count = $forbidden.Count
    forbidden_files_sample = @($forbidden | Select-Object -First 25 -ExpandProperty artifact_path)
    forbidden_reasons = @(($forbidden | ForEach-Object { $_.forbidden_reasons }) | Sort-Object -Unique)
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InventoryPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SummaryPath) | Out-Null
[System.IO.File]::WriteAllText($InventoryPath, ($inventory | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($SummaryPath, ($summary | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))

Write-Host "Lasagna package inventory: $InventoryPath"
Write-Host "Lasagna package audit summary: $SummaryPath"
if ($summary.status -ne "success") {
    throw "Lasagna package audit failed."
}
