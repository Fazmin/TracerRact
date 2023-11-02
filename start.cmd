@echo off
rem Windows launcher for start.ps1, with the same options as start.sh (start.cmd --help lists them).
rem Bypass applies to this one run only, so it works without changing the PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
