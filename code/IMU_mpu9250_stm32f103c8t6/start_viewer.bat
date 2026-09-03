@echo off
cd /d "%~dp0"
python imu_realtime_viewer.py %*
if errorlevel 1 pause
