@echo off
cd /d "%~dp0"
python jy901p_attitude_viewer.py
if errorlevel 1 pause
