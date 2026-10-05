$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $PackageRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$DemoData = Join-Path $PackageRoot "demo_data"
$DemoOutput = Join-Path $PackageRoot "demo_figures"

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
& $PythonPath (Join-Path $PackageRoot "make_demo_data.py") --output $DemoData
& $PythonPath (Join-Path $PackageRoot "generate_thesis_figures.py") `
    --analysis-root $DemoData `
    --tasks-csv (Join-Path $DemoData "tasks.csv") `
    --output $DemoOutput

Write-Host "Demo validation finished. Open: $DemoOutput"

