@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_uart_loopback.ps1" -PortName COM1
echo.
pause
endlocal
