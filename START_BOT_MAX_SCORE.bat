@echo off
setlocal
set "LAUNCH_LABEL=max score: 7-ply through six empty cells"
set "TDL_SEARCH=7p limit=7p,7p,7p,7p,7p,7p,7p,6p,6p,6p,6p,6p,6p,6p,6p,6p"
call "%~dp0START_BOT_SLOW.bat"
exit /b %ERRORLEVEL%
