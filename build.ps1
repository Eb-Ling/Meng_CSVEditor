param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE : $Program"
    }
}

try {
    if (-not (Test-Path -LiteralPath $projectPython)) {
        if ($PythonExecutable) {
            Invoke-Checked $PythonExecutable @("-m", "venv", ".venv")
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            Invoke-Checked "py" @("-3", "-m", "venv", ".venv")
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Invoke-Checked "python" @("-m", "venv", ".venv")
        } else {
            throw "Install Python 3.10 or newer, or pass -PythonExecutable with its full path."
        }
    }
    Write-Host "[1/3] Installing project dependencies..."
    Invoke-Checked $projectPython @("-m", "pip", "install", "-r", "requirements-build.txt")
    Write-Host "[2/3] Running regression tests..."
    $previousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        Invoke-Checked $projectPython @("-m", "unittest", "discover", "-s", "tests", "-v")
    } finally {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
    Write-Host "[3/3] Building Windows executable..."
    Invoke-Checked $projectPython @("-m", "PyInstaller", "--noconfirm", "Meng_CSVEditor.spec")
    Write-Host "Build complete: $PSScriptRoot\dist\Meng_CSVEditor.exe"
    exit 0
} catch {
    Write-Host "Build failed: $_" -ForegroundColor Red
    exit 1
}
