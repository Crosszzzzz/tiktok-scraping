$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    & '.venv\Scripts\python.exe' -m pip install -r requirements.txt
}
$huellaPort = if ($env:HUELLA_PORT) { $env:HUELLA_PORT } else { '8765' }
Write-Host "Huella: http://127.0.0.1:$huellaPort (Ctrl+C para cerrar)"
& '.venv\Scripts\python.exe' -m app
