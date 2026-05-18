# Build SynAgentPatent.exe — run from patent_ingestion_pipeline with venv active.
# Output: dist\SynAgentPatent.exe

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installing build tools..."
python -m pip install -q pyinstaller

$root = $PSScriptRoot
$templates = Join-Path $root "src\patent_pipeline\templates"
$static = Join-Path $root "src\patent_pipeline\static"
$data = Join-Path $root "data"

Write-Host "Building SynAgentPatent.exe..."
python -m PyInstaller `
  --noconfirm `
  --onefile `
  --name SynAgentPatent `
  --console `
  --add-data "$templates;templates" `
  --add-data "$static;static" `
  --add-data "$data;data" `
  --hidden-import=uvicorn.logging `
  --hidden-import=uvicorn.loops.auto `
  --hidden-import=uvicorn.protocols.http.auto `
  --hidden-import=uvicorn.lifespan.on `
  --hidden-import=scrapling `
  --collect-submodules=scrapling `
  --copy-metadata=scrapling `
  run_agent.py

Write-Host ""
Write-Host "Done: dist\SynAgentPatent.exe"
Write-Host "Example:"
Write-Host '  .\dist\SynAgentPatent.exe --data-dir D:\synagent_patent_data'
