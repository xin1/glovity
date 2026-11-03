@echo off
setlocal
call "%~dp0..\linkerhand\ros2\setup-ros.bat"
echo LinkerHand tactile dashboard: http://127.0.0.1:8765/
echo Keep this window open while using the hand.
"%~dp0..\linkerhand\ros2\.pixi\envs\default\python.exe" "%~dp0linkerhand_bridge.py" --host 127.0.0.1 --port 8765 --hand left --open
pause
