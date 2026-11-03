# LinkerHand 相关文件

- `ros2/`：ROS 2环境和驱动启动脚本；
- `cpp-sdk/`：厂商 C++ SDK及 PCAN依赖；
- `tools/diagnose_linkerhand_can.py`：CAN/PCAN诊断；
- `../web/tests/linkerhand_bridge_smoke.py`：Web 运动桥冒烟测试；
- `docs/LINKERHAND_L25_学习与使用记录.md`：学习和调试记录；
- `../web/start-linkerhand-web.bat`：启动 ROS 2触觉桥并打开网页。

常用启动顺序：

```text
1. linkerhand\ros2\start-driver.bat
2. web\start-linkerhand-web.bat
3. 浏览器打开 http://127.0.0.1:8765/
```
