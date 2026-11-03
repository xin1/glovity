@echo off
setlocal
title TeleX Web Interface - No ROS

set "WEB_DIR=%~dp0"
set "WEB_URL=http://127.0.0.1:8765/"
set "PYTHON_CMD="

where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [ERROR] Python was not found.
  echo Install Python or add py/python to PATH, then run this file again.
  goto :failed
)

if not exist "%WEB_DIR%index.html" (
  echo [ERROR] Web interface was not found:
  echo         %WEB_DIR%index.html
  goto :failed
)

echo TeleX web interface: %WEB_URL%
echo ROS2 is disabled. Live LinkerHand data and hand control are unavailable.
echo Simulation input and Web Serial can still be used.
echo Keep this window open. Press Ctrl+C to stop the server.
echo.

pushd "%WEB_DIR%"
start "" "%WEB_URL%"
%PYTHON_CMD% -m http.server 8765 --bind 127.0.0.1
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] The web server stopped with exit code %EXIT_CODE%.
  echo Port 8765 may already be in use.
  goto :failed
)
goto :end

:failed
echo.
echo Please keep this window open and check the error above.
pause
exit /b 1

:end
endlocal
