@echo off
setlocal
cd /d "%~dp0host"
"C:\Users\Bingxu_Ma\AppData\Local\Programs\Python\Python313\python.exe" -m awtest.tactile --sensor-port COM7 --device-id 3 --allow-err-len --motor-port COM32 --config tactile_com7_ch4.json --seconds 60 --log tactile_last_run.jsonl
pause
