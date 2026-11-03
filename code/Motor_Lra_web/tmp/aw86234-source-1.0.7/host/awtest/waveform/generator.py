# -*- coding: utf-8 -*-
"""波形生成器：7 种 shape、整数周期换算、有符号 8 位样本打包。

shape 定义与固件侧 ``WAVE_GEN``（协议 §9.8）完全一致：
``0``=正弦 ``1``=方波 ``2``=三角 ``3``=锯齿 ``4``=正弦+上升包络
``5``=正弦+下降包络 ``6``=点击（单周期指数衰减）。

设计要点（对应 `05` 文档 §5.5）：

* **一个周期的样本数必须是整数**，否则相邻周期之间会有相位跳变。
  :func:`periods_for` 返回 ``(周期样本数, 实际频率)``，除不尽时向上取整，
  界面据此提示"实际频率 = 采样率 / N"。
* 幅度输入范围 ``0..127``，输出统一裁剪到 ``-127..127``。
* 输出为**有符号**样本列表；下发给设备时用 :func:`to_bytes` 转成 2 的补码字节
  （与芯片 SRAM 中的格式一致，协议 §9.8 ``WAVE_ADD``）。

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple, Union

from ..protocol import commands as C

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

#: shape 编号 -> 中文名（与 ``protocol.commands.SHAPE_NAMES`` 同一份定义）
SHAPES: Dict[int, str] = dict(C.SHAPE_NAMES)

#: 中文名 -> shape 编号
SHAPE_CODES: Dict[str, int] = {name: code for code, name in SHAPES.items()}

#: 有符号 8 位样本的裁剪边界（协议里幅度字段是 7 位，故上限取 127）
SAMPLE_MIN = -127
SAMPLE_MAX = 127

ShapeLike = Union[int, str]


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def clip(value: float) -> int:
    """把任意数值裁剪成 ``-127..127`` 的整数采样。"""
    v = int(round(float(value)))
    if v < SAMPLE_MIN:
        return SAMPLE_MIN
    if v > SAMPLE_MAX:
        return SAMPLE_MAX
    return v


def shape_code(name_or_code: ShapeLike) -> int:
    """把 shape 的名称或编号统一成编号。

    接受 ``0..6`` 的整数、``"正弦"`` 这类中文名、或名字的子串
    （如 ``"锯齿"`` / ``"点击"``），无法识别时抛 ``ValueError``。
    """
    if isinstance(name_or_code, bool):
        raise ValueError(f"未知波形类型：{name_or_code!r}")
    if isinstance(name_or_code, int):
        if name_or_code in SHAPES:
            return name_or_code
        raise ValueError(f"未知波形类型编号：{name_or_code}（支持 0..{max(SHAPES)}）")
    if isinstance(name_or_code, str):
        text = name_or_code.strip()
        if not text:
            raise ValueError("波形类型不能为空")
        if text in SHAPE_CODES:
            return SHAPE_CODES[text]
        for code, name in SHAPES.items():
            if text in name:
                return code
        try:                                    # 允许传 "0x00" / "3" 这类字符串
            return shape_code(int(text, 0))
        except ValueError:
            pass
        raise ValueError(f"未知波形类型：{name_or_code!r}（支持 {list(SHAPES.values())}）")
    raise ValueError(f"未知波形类型：{name_or_code!r}")


def periods_for(freq_hz: float, sample_rate: int) -> Tuple[int, float]:
    """返回 ``(一个周期的样本数, 实际频率)``。

    规则（`05` 文档 §5.5）：
    ``n = ceil(sample_rate / freq_hz)``，且 ``n >= 2``；
    实际频率 = ``sample_rate / n``。除不尽时实际频率会略低于目标频率，
    界面应把实际值显示出来。
    """
    sr = int(sample_rate)
    f = float(freq_hz)
    if sr <= 0:
        raise ValueError("采样率必须为正数")
    if not (f > 0):
        raise ValueError("频率必须为正数")
    # 减去一个极小量，避免整除时因浮点误差多取一个样本（如 24000/1000 = 24.000000000000004）
    n = int(math.ceil(sr / f - 1e-9))
    if n < 2:
        n = 2
    if n > sr:
        n = sr
    return n, sr / n


def _rise_envelope(i: int, total: int) -> float:
    """线性上升包络 0 -> 1。"""
    return i / float(total - 1) if total > 1 else 1.0


def _fall_envelope(i: int, total: int) -> float:
    """线性下降包络 1 -> 0。"""
    return 1.0 - (i / float(total - 1)) if total > 1 else 1.0


def _click_envelope(i: int, total: int) -> float:
    """点击包络：单周期内指数衰减（衰减到约 0.7%）。"""
    return math.exp(-5.0 * (i / float(total - 1))) if total > 1 else 1.0


def generate(shape: ShapeLike, freq_hz: float, amplitude: int, cycles: int,
             sample_rate: int, phase_deg: float = 0.0) -> List[int]:
    """生成一段有符号 8 位样本（``-127..127``）。

    :param shape: 波形类型（编号或中文名，见 :data:`SHAPES`）
    :param freq_hz: 目标频率（Hz）
    :param amplitude: 峰值幅度 ``0..127``
    :param cycles: 周期数（``>= 1``）
    :param sample_rate: 采样率（Hz）
    :param phase_deg: 起始相位（度），用于多段波形拼接时的相位对齐
    :return: 样本列表，长度为 ``periods_for(freq_hz, sample_rate)[0] * cycles``
             （shape=6 点击波形固定为**一个**周期）

    shape 说明：

    ===== ==========================================
    编号  波形
    ===== ==========================================
    0     正弦 ``amp·sin(2π·t)``
    1     方波（前半周期 +amp，后半周期 −amp）
    2     三角波（−amp → +amp → −amp）
    3     锯齿波（−amp → +amp，单周期内单调上升）
    4     正弦 × 线性上升包络
    5     正弦 × 线性下降包络
    6     点击：单个周期的正弦 × 指数衰减包络
    ===== ==========================================
    """
    code = shape_code(shape)
    amp = int(amplitude)
    if amp < 0 or amp > SAMPLE_MAX:
        raise ValueError(f"幅度必须在 0..{SAMPLE_MAX}，实际 {amplitude}")
    cyc = int(cycles)
    if cyc < 1:
        raise ValueError(f"周期数必须 ≥1，实际 {cycles}")

    n, _actual = periods_for(freq_hz, sample_rate)
    # 点击是"单周期"事件，忽略 cycles（协议 §9.8：单周期衰减）
    single = (code == 6)
    total = n if single else n * cyc
    phase = (float(phase_deg) % 360.0) / 360.0

    out: List[int] = []
    for i in range(total):
        pos = i / float(n) + phase         # 以周期为单位的相位
        frac = pos % 1.0                   # 单周期内的相位 0..1
        if code == 0:
            value = math.sin(2.0 * math.pi * pos)
        elif code == 1:
            value = 1.0 if frac < 0.5 else -1.0
        elif code == 2:
            value = 4.0 * frac - 1.0 if frac < 0.5 else 3.0 - 4.0 * frac
        elif code == 3:
            value = 2.0 * frac - 1.0
        elif code == 4:
            value = math.sin(2.0 * math.pi * pos) * _rise_envelope(i, total)
        elif code == 5:
            value = math.sin(2.0 * math.pi * pos) * _fall_envelope(i, total)
        else:                              # code == 6
            value = math.sin(2.0 * math.pi * frac) * _click_envelope(i, total)
        out.append(clip(amp * value))
    return out


def to_bytes(samples: Iterable[int]) -> bytes:
    """有符号样本 -> 2 的补码字节（下发给 ``WAVE_ADD`` 用）。"""
    return bytes((int(s) & 0xFF) for s in samples)


def from_bytes(data: Sequence[int]) -> List[int]:
    """2 的补码字节 -> 有符号样本（``WAVE_READBACK`` 回读比对用）。"""
    return [b - 256 if b > 127 else b for b in bytes(data)]


def peak(samples: Sequence[int]) -> int:
    """返回样本的峰值（绝对值最大值）。"""
    return max((abs(int(s)) for s in samples), default=0)
