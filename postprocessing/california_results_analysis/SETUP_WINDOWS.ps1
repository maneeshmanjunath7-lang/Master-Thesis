param()

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvRoot = Join-Path $PackageRoot ".venv-post"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3.12 -m venv $VenvRoot
        if ($LASTEXITCODE -ne 0) {
            & py.exe -3.11 -m venv $VenvRoot
        }
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv $VenvRoot
    } else {
        throw "Python 3.11 or 3.12 was not found. Install Python from python.org and rerun this script."
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "The Python environment could not be created at $VenvRoot"
}

& $VenvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Python packaging-tool installation failed." }

& $VenvPython -m pip install -r (Join-Path $PackageRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Post-processing dependency installation failed." }

& $VenvPython -c "import matplotlib,numpy,pandas,pyarrow,scipy,seaborn; print('Dependency check: PASS')"
if ($LASTEXITCODE -ne 0) { throw "Dependency import check failed." }

& $VenvPython -m unittest discover -s (Join-Path $PackageRoot "tests") -v
if ($LASTEXITCODE -ne 0) { throw "Package self-tests failed." }

Write-Host ""
Write-Host "Full891 laptop post-processing environment is ready." -ForegroundColor Green
Write-Host "Python: $VenvPython"
