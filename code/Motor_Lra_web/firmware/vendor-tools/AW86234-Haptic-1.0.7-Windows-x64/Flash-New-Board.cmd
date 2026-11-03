@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\flash.ps1" -Mode NewBoard
pause
