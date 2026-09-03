@echo off
setlocal
cd /d "%~dp0"

echo Starting STM32F103 + ICM-20948 realtime 3D viewer...
echo Close every serial monitor before continuing.
echo.

python imu_3d_visualizer.py

if errorlevel 1 (
    echo.
    echo The viewer exited with an error.
    echo Install dependencies with:
    echo python -m pip install -r requirements.txt
    pause
)

endlocal
