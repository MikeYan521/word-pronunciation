# Start the vocabulary trainer on Windows.
#   .\run.ps1            -> http://127.0.0.1:8000
#   .\run.ps1 -Port 8080
param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@("py", "-3") }
if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += ,@("python") }
$bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path $bundled) { $candidates += ,@($bundled) }

$chosen = $null
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $args = @()
    if ($candidate.Count -gt 1) { $args = $candidate[1..($candidate.Count - 1)] }
    try {
        & $exe @args -c "import pdfplumber, pypdf" 2>$null
        if ($LASTEXITCODE -eq 0) { $chosen = $candidate; break }
    } catch { }
}

if (-not $chosen) {
    Write-Host "未找到可用的 Python 环境（需要 pdfplumber 与 pypdf）。" -ForegroundColor Yellow
    Write-Host "请执行：py -3 -m pip install pdfplumber pypdf" -ForegroundColor Yellow
    exit 1
}

$exe = $chosen[0]
$argv = @()
if ($chosen.Count -gt 1) { $argv = $chosen[1..($chosen.Count - 1)] }

Write-Host "使用 Python: $exe $($argv -join ' ')" -ForegroundColor DarkGray
& $exe @argv run.py --host $HostAddress --port $Port
