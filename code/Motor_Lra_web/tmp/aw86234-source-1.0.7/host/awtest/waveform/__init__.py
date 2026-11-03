# -*- coding: utf-8 -*-
"""``awtest.waveform`` —— 波形模块（纯逻辑，无 GUI 依赖）。

子模块：

* :mod:`awtest.waveform.generator` —— 7 种 shape 的波形合成与字节打包
* :mod:`awtest.waveform.io_csv` —— CSV 导入导出
* :mod:`awtest.waveform.editor_model` —— 带撤销栈的编辑文档 + SRAM 预算
* :mod:`awtest.waveform.convert` —— 寄存器码值与物理量的换算

**本包及其子模块一律不得 import PySide6 / pyqtgraph**（`05` 文档 §2 硬性要求）。
"""

from __future__ import annotations

from . import convert
from .editor_model import (DEFAULT_BASE_ADDR, MAX_UNDO, SRAM_TOTAL_BYTES,
                           WaveformDoc, sram_budget)
from .generator import (SAMPLE_MAX, SAMPLE_MIN, SHAPES, clip, from_bytes,
                        generate, peak, periods_for, shape_code, to_bytes)
from .io_csv import LAST_LOAD_NOTES, load_csv, save_csv

__all__ = [
    "SHAPES", "SAMPLE_MIN", "SAMPLE_MAX",
    "shape_code", "periods_for", "generate", "to_bytes", "from_bytes",
    "clip", "peak",
    "load_csv", "save_csv", "LAST_LOAD_NOTES",
    "WaveformDoc", "sram_budget", "MAX_UNDO", "DEFAULT_BASE_ADDR", "SRAM_TOTAL_BYTES",
    "convert",
]
