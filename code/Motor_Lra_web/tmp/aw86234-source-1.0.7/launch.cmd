@echo off
setlocal
cd /d "%~dp0host"
if exist "C:\Users\Bingxu_Ma\AppData\Local\Programs\Python\Python313\pythonw.exe" (
    start "" "C:\Users\Bingxu_Ma\AppData\Local\Programs\Python\Python313\pythonw.exe" -m awtest
) else (
    python -m awtest
    if errorlevel 1 pause
)
