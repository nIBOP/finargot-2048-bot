@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo FINARGOT 2048 BOT - безопасный медленный запуск
echo ============================================================
echo.
echo Что будет дальше:
echo   1. Скрипт проверит Python-зависимости.
echo   2. Откроется Chrome.
echo   3. Если сайт попросит вход - войдите в BattlePass.
echo   4. Откройте миниигру 2048 и нажмите Play/Продолжить.
echo   5. Когда появится поле 4x4, бот начнет играть сам.
echo.
echo Важно:
echo   - Не закрывайте Chrome во время игры.
echo   - Не нажимайте стрелки вручную.
echo   - После конца игры дождитесь сохранения результата на сайте.
echo.

if not exist "bot_final.py" (
  echo [ОШИБКА] Не найден bot_final.py. Запускайте этот файл из папки бота.
  pause
  exit /b 1
)

if not exist "external\TDL2048\tdl2048.exe" (
  echo [ОШИБКА] Не найден external\TDL2048\tdl2048.exe.
  echo Соберите билд заново или проверьте папку external\TDL2048.
  pause
  exit /b 1
)

if not exist "external\TDL2048\8x6patt.w" (
  echo [ОШИБКА] Не найдена модель external\TDL2048\8x6patt.w.
  echo Без нее лучший TDL-режим не запустится.
  pause
  exit /b 1
)

set "PYTHON_CMD=python"
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

echo [1/2] Проверяю зависимости Python...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Не удалось установить зависимости.
  echo Проверьте, что установлен Python 3 и есть доступ в интернет.
  pause
  exit /b 1
)

echo.
echo [2/2] Запускаю бота. Дальше следуйте подсказкам в этой консоли.
echo.
%PYTHON_CMD% -u bot_final.py ^
  --browser chrome ^
  --solver-backend tdl ^
  --tdl-network 8x6patt ^
  --tdl-search 3p ^
  --tile-encoding auto ^
  --delay 0.20 0.42 ^
  --rest-every 300 ^
  --rest-delay 4 10 ^
  --after-move-timeout 1.2 ^
  --log-dir runs\battle_tdl_8x6_3p_slow ^
  --post-game-hold 900 ^
  --error-hold 300

echo.
echo Бот завершился. Если выше была ошибка, пришлите файл лога из папки runs.
pause
