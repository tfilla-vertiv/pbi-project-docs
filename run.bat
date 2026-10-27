@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%
