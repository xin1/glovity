# Web

网页相关内容统一放在这里：

- `index.html`、`style.css`、`app.js`：页面结构、样式和交互；
- `telex-device.js`：TELEX 串口协议；
- `linkerhand_bridge.py`：ROS 2 与网页之间的数据桥；
- `start-linkerhand-web.bat`：启动 LinkerHand 数据桥并打开网页；
- `tests/`：网页和桥接程序测试。

使用 LinkerHand 时，先运行 `..\linkerhand\ros2\start-driver.bat`，再运行
`start-linkerhand-web.bat`。
