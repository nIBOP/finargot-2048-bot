$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootPath = (Resolve-Path -LiteralPath $root).Path
$distRoot = Join-Path $rootPath "dist"
$package = Join-Path $distRoot "finargot-bot"
$zip = Join-Path $distRoot "finargot-bot.zip"

function Assert-InRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathToCheck
    )
    $fullPath = [System.IO.Path]::GetFullPath($PathToCheck)
    if (-not $fullPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside project root: $fullPath"
    }
}

Assert-InRoot $distRoot
Assert-InRoot $package
Assert-InRoot $zip

Write-Host "==> Building FINARGOT 2048 bot package"
Write-Host "Project: $rootPath"

New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
if (Test-Path -LiteralPath $package) {
    Write-Host "Removing old package folder: $package"
    Remove-Item -LiteralPath $package -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $package | Out-Null

$rootFiles = @(
    "bot_final.py",
    "main.py",
    "requirements.txt",
    "README_RU.txt",
    "START_BOT_SLOW.bat"
)

foreach ($file in $rootFiles) {
    $src = Join-Path $rootPath $file
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Missing required file: $src"
    }
    Copy-Item -LiteralPath $src -Destination (Join-Path $package $file) -Force
}

$tdlSrc = Join-Path $rootPath "external\TDL2048"
$tdlDst = Join-Path $package "external\TDL2048"
New-Item -ItemType Directory -Force -Path $tdlDst | Out-Null

$tdlFiles = @(
    "tdl2048.exe",
    "8x6patt.w",
    "LICENSE.md",
    "README.md"
)

foreach ($file in $tdlFiles) {
    $src = Join-Path $tdlSrc $file
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Missing TDL2048 file: $src"
    }
    Write-Host "Copying $file"
    Copy-Item -LiteralPath $src -Destination (Join-Path $tdlDst $file) -Force
}

$rustSolver = Join-Path $rootPath "target\release\solver2048.exe"
if (Test-Path -LiteralPath $rustSolver) {
    $rustDst = Join-Path $package "target\release"
    New-Item -ItemType Directory -Force -Path $rustDst | Out-Null
    Copy-Item -LiteralPath $rustSolver -Destination (Join-Path $rustDst "solver2048.exe") -Force
}

if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}

Write-Host "Creating archive: $zip"
$archiveCreated = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        if (Test-Path -LiteralPath $zip) {
            Remove-Item -LiteralPath $zip -Force
        }

        $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
        if ($tar) {
            & $tar.Source -a -cf $zip -C $distRoot "finargot-bot"
            if ($LASTEXITCODE -ne 0) {
                throw "tar.exe failed with exit code $LASTEXITCODE"
            }
        } else {
            Compress-Archive -LiteralPath $package -DestinationPath $zip -Force
        }

        $archiveCreated = $true
        break
    } catch {
        Write-Warning "Archive attempt $attempt failed: $($_.Exception.Message)"
        if ($attempt -lt 3) {
            Start-Sleep -Seconds 3
        }
    }
}

$packageSize = (Get-ChildItem -LiteralPath $package -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host "Done."
Write-Host ("Folder: {0} ({1:N1} MB)" -f $package, ($packageSize / 1MB))
if ($archiveCreated -and (Test-Path -LiteralPath $zip)) {
    $zipSize = (Get-Item -LiteralPath $zip).Length
    Write-Host ("Archive: {0} ({1:N1} MB)" -f $zip, ($zipSize / 1MB))
} else {
    Write-Warning "Archive was not created. The folder build is still ready: $package"
}
