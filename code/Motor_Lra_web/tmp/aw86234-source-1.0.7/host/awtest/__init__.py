# -*- coding: utf-8 -*-
"""AW86234 八路振动测试板 —— PC 上位机（PySide6）。

分层：
    awtest.protocol   帧编解码 / 传输层（无 GUI 依赖，可单测）
    awtest.device     面向对象的高层设备 API
    awtest.profiles   参数 Profile 的 dataclass + JSON 存取
    awtest.waveform   波形生成 / CSV 导入导出 / 编码（无 GUI 依赖）
    awtest.script     测试脚本引擎与报告导出（无 GUI 依赖）
    awtest.ui         PySide6 界面
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
