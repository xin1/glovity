@echo off
setlocal
title LinkerHand Tactile Dashboard

set "WEB_DIR=%~dp0"
set "ROS_DIR=%~dp0..\..\github\glovity\code\Motor_Lra_web\linkerhand\ros2"
set "ROS_SETUP=%ROS_DIR%\setup-ros.bat"
set "ROS_PYTHON=%ROS_DIR%\.pixi\envs\default\python.exe"
set "BRIDGE=%WEB_DIR%linkerhand_bridge.py"

if not exist "%ROS_SETUP%" (
  echo [ERROR] ROS2 setup script was not found:
  echo         %ROS_SETUP%
  goto :failed
)

if not exist "%ROS_PYTHON%" (
  echo [ERROR] ROS2 Python was not found:
  echo         %ROS_PYTHON%
  goto :failed
)

if not exist "%BRIDGE%" (
  echo [ERROR] Web bridge was not found:
  echo         %BRIDGE%
  goto :failed
)

call "%ROS_SETUP%"
if errorlevel 1 (
  echo [ERROR] Failed to initialize the ROS2 environment.
  goto :failed
)

echo.
echo LinkerHand tactile dashboard: http://127.0.0.1:8765/
echo Keep this window open while using the interface.
echo Press Ctrl+C to stop the server.
echo.

pushd "%WEB_DIR%"
"%ROS_PYTHON%" "%BRIDGE%" --host 127.0.0.1 --port 8765 --hand left --open
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] The dashboard stopped with exit code %EXIT_CODE%.
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
