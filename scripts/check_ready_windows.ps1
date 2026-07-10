param(
    [string]$Network = "8x6patt",
    [string]$Search = "1p"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tdlDir = Join-Path $root "external\TDL2048"
$tdlExe = Join-Path $tdlDir "tdl2048.exe"
$tdlModel = Join-Path $tdlDir "$Network.w"

function Pass {
    param([string]$Message)
    Write-Host "[OK] $Message"
}

function Require-Path {
    param([string]$Path, [string]$Message)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Message Missing: $Path"
    }
    Pass $Message
}

function Read-Line-WithTimeout {
    param(
        [System.IO.TextReader]$Reader,
        [int]$TimeoutMs,
        [string]$Step
    )
    $task = $Reader.ReadLineAsync()
    if (-not $task.Wait($TimeoutMs)) {
        throw "$Step timed out after $TimeoutMs ms"
    }
    if ($null -eq $task.Result) {
        throw "$Step returned no output"
    }
    return $task.Result.Trim()
}

Write-Host "FINARGOT 2048 readiness check"
Write-Host "Root: $root"
Write-Host ""

$pythonCandidates = @(
    @{ Exe = "python"; Args = @() },
    @{ Exe = "py"; Args = @("-3") }
)
$pythonExe = $null
$pythonArgs = @()
$pythonTried = @()

foreach ($candidate in $pythonCandidates) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
        continue
    }
    $versionOutput = & $candidate.Exe @($candidate.Args) -c "import sys; ok=sys.version_info >= (3,10); print(sys.executable); print(sys.version.split()[0]); raise SystemExit(0 if ok else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $pythonTried += "$($candidate.Exe) $($candidate.Args -join ' ') version is too old"
        continue
    }
    $importOutput = & $candidate.Exe @($candidate.Args) -c "import selenium, webdriver_manager; print('selenium imports OK')" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonExe = $candidate.Exe
        $pythonArgs = @($candidate.Args)
        Pass "Python is available: $($versionOutput -join ' ')"
        Pass "Python dependencies import"
        break
    }
    $pythonTried += "$($candidate.Exe) $($candidate.Args -join ' ') has no selenium/webdriver_manager"
}

if (-not $pythonExe) {
    if ($pythonTried.Count -eq 0) {
        throw "Python not found. Install Python 3.10+."
    }
    throw "Python dependencies are missing. Run: python -m pip install -r requirements.txt. Tried: $($pythonTried -join '; ')"
}

$chromeCandidates = @(@(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LocalAppData "Google\Chrome\Application\chrome.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

$chromeCommand = Get-Command "chrome.exe" -ErrorAction SilentlyContinue
if ($chromeCandidates.Count -gt 0) {
    Pass "Chrome found: $($chromeCandidates[0])"
} elseif ($chromeCommand) {
    Pass "Chrome found in PATH: $($chromeCommand.Source)"
} else {
    throw "Google Chrome not found."
}

Require-Path $tdlExe "TDL2048 executable found"
Require-Path $tdlModel "TDL2048 model found"

$msysBins = @(
    "C:\msys64\ucrt64\bin",
    "C:\msys64\usr\bin",
    "C:\msys64\mingw64\bin"
) | Where-Object { Test-Path -LiteralPath $_ }

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $tdlExe
$psi.WorkingDirectory = $tdlDir
$psi.Arguments = "--protocol -n $Network -i `"$tdlModel`" -S $Search"
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
if ($msysBins.Count -gt 0) {
    $psi.EnvironmentVariables["PATH"] = ($msysBins -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $psi.EnvironmentVariables["PATH"]
}

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $psi
[void]$process.Start()

try {
    $ready = Read-Line-WithTimeout $process.StandardOutput 20000 "TDL READY"
    if ($ready -ne "READY") {
        throw "TDL did not return READY. Got: $ready"
    }
    Pass "TDL protocol returned READY"

    $process.StandardInput.WriteLine("SOLVE 0000000000000011")
    $process.StandardInput.Flush()
    $response = Read-Line-WithTimeout $process.StandardOutput 20000 "TDL SOLVE"
    if ($response -notmatch "^OK ") {
        throw "TDL SOLVE failed. Got: $response"
    }
    Pass "TDL protocol solved test board: $response"

    $process.StandardInput.WriteLine("QUIT")
    $process.StandardInput.Flush()
    if (-not $process.WaitForExit(3000)) {
        $process.Kill()
    }
} finally {
    if (-not $process.HasExited) {
        $process.Kill()
    }
}

Write-Host ""
Write-Host "Ready: bot files, Python, Chrome, TDL executable, model and protocol are OK."
