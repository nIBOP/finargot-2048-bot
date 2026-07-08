param(
    [string]$Network = "4x6patt",
    [int]$EpisodesK = 1000,
    [int]$Threads = 0,
    [string]$InputModel = "",
    [string]$OutputModel = "",
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tdlDir = Join-Path $root "external\TDL2048"
$exe = Join-Path $tdlDir "tdl2048.exe"

if (-not (Test-Path -LiteralPath $exe)) {
    throw "tdl2048.exe not found. Run: powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1"
}

if (-not $OutputModel) {
    $OutputModel = Join-Path $tdlDir "$Network-trained.w"
}
if (-not $LogFile) {
    $LogFile = Join-Path $tdlDir "$Network-training.x"
}

$argsList = @("-n", $Network, "-t", $EpisodesK.ToString())
if ($Threads -gt 0) {
    $argsList += @("-p", $Threads.ToString())
}
if ($InputModel) {
    $argsList += @("-i", $InputModel)
}
$argsList += @("-o", $OutputModel, $LogFile)

Push-Location $tdlDir
try {
    Write-Host "Training TDL2048 model..."
    Write-Host "$exe $($argsList -join ' ')"
    & $exe @argsList
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Training finished:"
Write-Host "  model: $OutputModel"
Write-Host "  log:   $LogFile"
