@echo off
cd /d "%~dp0"
python jy901p_3d_viewer.py
if errorlevel 1 pause
