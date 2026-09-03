@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py motor_ui.py
) else (
    python motor_ui.py
)
if errorlevel 1 pause
