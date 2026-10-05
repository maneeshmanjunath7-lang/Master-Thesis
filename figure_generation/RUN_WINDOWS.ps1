param(
    [Parameter(Mandatory=$true)]
    [string]$AnalysisRoot,

    [Parameter(Mandatory=$true)]
    [string]$TasksCsv,

    [string]$Output = ".\thesis_figures"
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $PackageRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv $VenvPath
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv $VenvPath
    } else {
        throw "Python 3 was not found. Install Python 3.11 or newer, then run this script again."
    }
}

& $PythonPath -m pip install --upgrade pip
& $PythonPath -m pip install -r (Join-Path $PackageRoot "requirements.txt")
& $PythonPath (Join-Path $PackageRoot "generate_thesis_figures.py") `
    --analysis-root $AnalysisRoot `
    --tasks-csv $TasksCsv `
    --output $Output

if ($LASTEXITCODE -ne 0) {
    throw "Figure generation failed. Read the error above; no existing thesis figures were overwritten."
}

Write-Host "Finished. Figures and caption guide are in: $Output"

