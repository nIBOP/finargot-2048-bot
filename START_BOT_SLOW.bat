@echo off
setlocal
cd /d "%~dp0"

if not defined TDL_SEARCH set "TDL_SEARCH=7p limit=7p,7p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p,6p"
if not defined LAUNCH_LABEL set "LAUNCH_LABEL=balanced"

echo ============================================================
echo FINARGOT 2048 BOT - battle launch
echo Search profile: %LAUNCH_LABEL%
echo ============================================================
echo.
echo What happens next:
echo   1. This script checks Python dependencies.
echo   2. Chrome opens.
echo   3. If BattlePass asks for login, log in.
echo   4. Open the 2048 mini-game and press Play/Continue.
echo   5. When the 4x4 board is visible, the bot starts playing.
echo.
echo Important:
echo   - Do not close Chrome during the game.
echo   - Do not press arrow keys manually.
echo   - After game over, wait for the site to save the result.
echo   - Human rhythm is enabled: pauses are irregular.
echo   - There is no automatic score or move cutoff in this launcher.
echo.

if not exist "bot_final.py" (
  echo [ERROR] bot_final.py not found. Run this file from the bot folder.
  pause
  exit /b 1
)

if not exist "external\TDL2048\tdl2048.exe" (
  echo [ERROR] external\TDL2048\tdl2048.exe not found.
  echo Run: powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
  pause
  exit /b 1
)

if not exist "external\TDL2048\8x6patt.w" (
  echo [ERROR] external\TDL2048\8x6patt.w model not found.
  echo Run: powershell -ExecutionPolicy Bypass -File .\scripts\setup_tdl_windows.ps1
  pause
  exit /b 1
)

set "PYTHON_CMD="
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
  )
)
if not defined PYTHON_CMD (
  echo [ERROR] Python 3.10 or newer was not found.
  pause
  exit /b 1
)

echo [1/2] Checking Python dependencies...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Could not install Python dependencies.
  echo Check Python and internet access.
  pause
  exit /b 1
)

echo.
echo [2/2] Starting the bot. Follow the console and Chrome prompts.
echo.
%PYTHON_CMD% -u bot_final.py ^
  --browser chrome ^
  --solver-backend tdl ^
  --tdl-network 8x6patt ^
  --tdl-search "%TDL_SEARCH%" ^
  --tdl-cache "256M" ^
  --tdl-cache-peek ^
  --tdl-downgrade-threshold 32768 ^
  --tile-encoding auto ^
  --rhythm-profile human ^
  --after-move-timeout 1.2 ^
  --max-stalled-moves 3 ^
  --max-missing-board-reads 3 ^
  --log-dir runs\battle_tdl_8x6_deep_human ^
  --post-game-hold 900 ^
  --error-hold 300

echo.
echo Bot process ended. If there was an error, send the latest log from runs.
pause
