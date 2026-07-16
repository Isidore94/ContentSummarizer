$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repo ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install requirements-build.txt first."
}

Push-Location $repo
try {
    & $python -m PyInstaller --noconfirm --clean (Join-Path $repo "ContentSummarizer.spec")
} finally {
    Pop-Location
}

$dist = Join-Path $repo "dist"
Copy-Item -LiteralPath (Join-Path $repo ".env.example") -Destination (Join-Path $dist ".env.example") -Force
Copy-Item -LiteralPath (Join-Path $repo "EXE_SETUP.md") -Destination (Join-Path $dist "EXE_SETUP.md") -Force

Write-Host ""
Write-Host "Built: $dist\ContentSummarizer.exe"
Write-Host "Copy your private .env beside the executable before launching it."
