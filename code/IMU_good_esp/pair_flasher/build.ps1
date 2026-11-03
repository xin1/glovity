$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $here '.venv')
}
& $python -m pip install -r (Join-Path $here 'requirements.txt')
& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name 'Glovity_Pair_Flasher' `
    --paths $here `
    --distpath (Join-Path $here 'dist') `
    --workpath (Join-Path $here 'build') `
    --specpath $here `
    (Join-Path $here 'app.py')
Write-Host "EXE: $(Join-Path $here 'dist\Glovity_Pair_Flasher.exe')"
