@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0query_imu_version.ps1"
echo.
pause
endlocal
