$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python -m unittest discover -s fixtures -p test_evaluate.py -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python scripts/check_public.py
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
