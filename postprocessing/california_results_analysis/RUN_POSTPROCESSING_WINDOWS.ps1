param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$InputPath,

    [Parameter(Position = 1)]
    [string]$OutputPath = "",

    [switch]$CaseOnly,
    [switch]$KeepExtracted
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $PackageRoot ".venv-post\Scripts\python.exe"
$Analyzer = Join-Path $PackageRoot "postprocess_california.py"

if (-not (Test-Path -LiteralPath $InputPath)) {
    throw "Input not found: $InputPath"
}
$ResolvedInput = (Resolve-Path -LiteralPath $InputPath).Path

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackageRoot "SETUP_WINDOWS.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Environment setup failed." }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $env:USERPROFILE (
        "Full891_Postprocessing\California_" + (Get-Date -Format "yyyyMMdd_HHmmss")
    )
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

$WorkBase = Join-Path $env:LOCALAPPDATA "Full891Postprocess\work"
New-Item -ItemType Directory -Path $WorkBase -Force | Out-Null
$RunId = (Get-Date -Format "yyyyMMdd_HHmmss") + "_" + [guid]::NewGuid().ToString("N")
$WorkDir = Join-Path $WorkBase $RunId
New-Item -ItemType Directory -Path $WorkDir | Out-Null

$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
$env:MPLCONFIGDIR = Join-Path $env:LOCALAPPDATA "Full891Postprocess\matplotlib"

$Arguments = @(
    $Analyzer,
    "--input", $ResolvedInput,
    "--output", $OutputPath,
    "--work-dir", $WorkDir
)
if ($CaseOnly) {
    $Arguments += "--skip-task-metrics"
}

Write-Host "Input:  $ResolvedInput"
Write-Host "Output: $OutputPath"
Write-Host ""

$Succeeded = $false
try {
    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Post-processing exited with code $LASTEXITCODE"
    }
    $Succeeded = $true
} finally {
    if ($Succeeded -and -not $KeepExtracted -and (Test-Path -LiteralPath $WorkDir)) {
        $ResolvedBase = (Resolve-Path -LiteralPath $WorkBase).Path
        $ResolvedWork = (Resolve-Path -LiteralPath $WorkDir).Path
        if ((Split-Path -Parent $ResolvedWork) -ne $ResolvedBase) {
            throw "Refusing to clean an unexpected work directory: $ResolvedWork"
        }
        Remove-Item -LiteralPath $ResolvedWork -Recurse -Force
    }
}

Write-Host ""
Write-Host "Post-processing completed successfully." -ForegroundColor Green
Write-Host "Results: $OutputPath"
Write-Host "Start with postprocessing_run_summary.json, then figures and tables."
