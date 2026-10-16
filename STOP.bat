@echo off
setlocal
if not "%~1"=="" exit /b 2
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lifecycle.ps1" stop
set TASK_EXIT=%errorlevel%
if not "%TASK_EXIT%"=="0" pause
exit /b %TASK_EXIT%
