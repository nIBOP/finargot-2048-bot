param(
    [string]$Network = "8x6patt",
    [switch]$SkipModel,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tdlDir = Join-Path $root "external\TDL2048"
$patch = Join-Path $root "patches\tdl2048-protocol.patch"
$modelUrl = "https://moporgic.info/2048/model/$Network.w.xz"
$modelXz = Join-Path $tdlDir "$Network.w.xz"
$model = Join-Path $tdlDir "$Network.w"

function Require-Command {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found. $InstallHint"
    }
}

Require-Command "git" "Install Git for Windows: https://git-scm.com/download/win"

New-Item -ItemType Directory -Force -Path (Join-Path $root "external") | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $tdlDir ".git"))) {
    Write-Host "Cloning moporgic/TDL2048..."
    git clone https://github.com/moporgic/TDL2048 $tdlDir
}

Push-Location $tdlDir
try {
    if (-not (Select-String -Path "2048.cpp" -Pattern "downgrade_threshold" -Quiet)) {
        if (Select-String -Path "2048.cpp" -Pattern "--protocol" -Quiet) {
            throw "An older TDL protocol patch is present. Remove external\\TDL2048 and run this setup script again."
        }
        Write-Host "Applying protocol patch..."
        git apply $patch
    } else {
        Write-Host "Current 80-bit protocol patch already applied."
    }

    if (-not $SkipModel -and -not (Test-Path -LiteralPath $model)) {
        if (-not (Test-Path -LiteralPath $modelXz)) {
            Write-Host "Downloading $Network model..."
            Invoke-WebRequest -Uri $modelUrl -OutFile $modelXz
        }
        Write-Host "Extracting $Network model..."
        tar -xf $modelXz
    }

    if (-not $SkipBuild) {
        $msysBins = @(
            "C:\msys64\ucrt64\bin",
            "C:\msys64\usr\bin",
            "C:\msys64\mingw64\bin"
        ) | Where-Object { Test-Path -LiteralPath $_ }
        if ($msysBins.Count -gt 0) {
            $env:PATH = ($msysBins -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH
        }

        if (-not (Get-Command "make" -ErrorAction SilentlyContinue)) {
            throw "make not found. Install MSYS2, then in MSYS2 UCRT64 run: pacman -S --needed mingw-w64-ucrt-x86_64-gcc make git curl xz"
        }
        if (-not (Get-Command "g++" -ErrorAction SilentlyContinue)) {
            throw "g++ not found. Install MSYS2, then in MSYS2 UCRT64 run: pacman -S --needed mingw-w64-ucrt-x86_64-gcc make git curl xz"
        }

        Write-Host "Building tdl2048.exe..."
        make OUTPUT=tdl2048
    }
} finally {
    Pop-Location
}

$exe = Join-Path $tdlDir "tdl2048.exe"
Write-Host ""
Write-Host "TDL restore result:"
Write-Host "  source: $tdlDir"
Write-Host "  exe:    $exe"
Write-Host "  model:  $model"
if (-not (Test-Path -LiteralPath $exe) -and -not $SkipBuild) {
    throw "tdl2048.exe was not created."
}
if (-not $SkipModel -and -not (Test-Path -LiteralPath $model)) {
    throw "$Network.w was not created."
}
Write-Host "Done."
