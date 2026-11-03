# -*- coding: utf-8 -*-
"""参数换算（寄存器码值与物理量之间）。

公式来源：``03_串口协议规范_v1.0.md`` §9.7 / §9.9 与附录 B，
以及 ``05_上位机需求规格_PySide6.md`` §5.4 的参数表。

覆盖的换算：

======================================  ==========================================
函数                                    公式
======================================  ==========================================
:func:`f_pre_to_f0` / :func:`f0_to_f_pre`  ``F0 = 24000 / F_PRE``
:func:`f0_from_raw`                      ``F0 = 192000 / (H*256 + L)``（一次性检测，实测修正）
:func:`drv_lvl_to_voltage`               ``V ≈ VDD * L / 128``
:func:`dsp_time_to_us` / :func:`us_to_dsp_time`  ``t = code / 48000``（秒）
:func:`gain_to_ratio`                    ``GAIN = code / 128``
:func:`avg_to_vdd`                       ``VDD = 6.1 * AVG / 1023``
:func:`avg_to_rl`                        ``RL = 610 * AVG / (1023 * D2S_GAIN)``
======================================  ==========================================

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import math
from typing import Optional

#: CONT 段 F_PRE 的换算系数：``F0 = F_PRE_BASE / F_PRE``
F_PRE_BASE = 24000.0

#: 一次性检测（0x24/0x25）的换算系数：``F0 = F0_RAW_BASE / raw``
#: ⚠️ 实测修正：实际基准是 192000（手册写 384000，相差 2 倍）。
#:    证据与说明见 :data:`awtest.protocol.commands.F0_RAW_BASE_ONESHOT`。
F0_RAW_BASE = 192000.0

#: 连续跟踪（0x26/0x27）的换算系数：``F0 = CONT_F0_RAW_BASE / raw``（与手册一致）
CONT_F0_RAW_BASE = 384000.0

#: DSP 时间单位（协议 §9.7：``t = code/48000`` 秒）
DSP_TIME_HZ = 48000.0

#: 电压检测换算系数（协议 §9.9）
VBAT_FULL_SCALE_V = 6.1
VBAT_ADC_FULL_SCALE = 1023.0

#: LRA 阻抗换算系数（协议 §9.9）
RL_FACTOR = 610.0

#: ``DETCFG2(0x50) bit2:0`` 在 RL 检测时的 D2S_GAIN 倍率。
#: ⚠️ 不是寄存器默认值 ×8 —— 固件按手册要求为 RL 检测显式写入 ×20
#: （对应固件 ``cfg_defaults.h`` 的 ``DEF_RL_D2S_GAIN = 6`` 与上位机
#: ``commands.DET_RL_D2S_GAIN``）。改动需三处同步。
DEFAULT_D2S_GAIN = 20


# --------------------------------------------------------------------------
# CONT 段：F_PRE <-> F0
# --------------------------------------------------------------------------


def f_pre_to_f0(f_pre: int) -> float:
    """``F0 = 24000 / F_PRE`` Hz（协议 §9.7）。``F_PRE = 0`` 非法。"""
    code = int(f_pre)
    if code <= 0:
        raise ValueError("F_PRE 必须在 1..255（0 会导致除零）")
    return F_PRE_BASE / code


def f0_to_f_pre(f0_hz: float, clamp: bool = True) -> int:
    """由目标 F0 反算 ``F_PRE = round(24000 / F0)``，并收敛到 ``1..255``。

    ``clamp=False`` 时超出范围抛 ``ValueError``。
    """
    hz = float(f0_hz)
    if not (hz > 0):
        raise ValueError("F0 必须为正数")
    code = int(round(F_PRE_BASE / hz))
    if clamp:
        return max(1, min(255, code))
    if not (1 <= code <= 255):
        raise ValueError(f"F0={hz} Hz 超出 F_PRE 1..255 可表示的范围（约 94~24000 Hz）")
    return code


# --------------------------------------------------------------------------
# 测量段：F0 原始值 <-> Hz
# --------------------------------------------------------------------------


def raw_to_u16(raw_h: int, raw_l: int) -> int:
    """``raw = H * 256 + L``。"""
    return ((int(raw_h) & 0xFF) << 8) | (int(raw_l) & 0xFF)


def f0_from_raw(raw_h: int, raw_l: int) -> Optional[float]:
    """``F0 = 384000 / (H*256 + L)`` Hz。

    ``raw == 0`` 时返回 ``None``（除零，必为无效值，协议 §9.9）。
    """
    raw = raw_to_u16(raw_h, raw_l)
    if raw == 0:
        return None
    return F0_RAW_BASE / raw


# --------------------------------------------------------------------------
# 驱动电平 / 增益
# --------------------------------------------------------------------------


def drv_lvl_to_voltage(level: int, vdd: float = 5.0) -> float:
    """由 ``DRV1_LVL``/``DRV2_LVL``（0..127）估算输出电压 ``VDD * L / 128``。"""
    code = int(level)
    if not (0 <= code <= 127):
        raise ValueError("DRVx_LVL 必须在 0..127")
    return float(vdd) * code / 128.0


def gain_to_ratio(code: int) -> float:
    """``GAIN = code / 128``（默认 128 = 1.00×），协议 §9.7。"""
    return int(code) / 128.0


# --------------------------------------------------------------------------
# DSP 时间码 <-> 微秒
# --------------------------------------------------------------------------


def dsp_time_to_us(code: int) -> float:
    """``t = code / 48000`` 秒 -> 微秒（``DRV_WIDTH`` / ``WAIT_NUM`` / ``START_DLY``）。"""
    return int(code) / DSP_TIME_HZ * 1e6


def us_to_dsp_time(us: float) -> int:
    """微秒 -> DSP 时间码 ``round(us * 48000 / 1e6)``。"""
    value = float(us)
    if value < 0:
        raise ValueError("时间不能为负")
    return int(round(value * DSP_TIME_HZ / 1e6))


# --------------------------------------------------------------------------
# DET_READ：电池电压 / LRA 阻抗
# --------------------------------------------------------------------------


def avg_to_vdd(avg_data: int) -> float:
    """``VDD = 6.1 * AVG_DATA / 1023`` V（协议 §9.9，``DET_READ seq=0``）。"""
    return VBAT_FULL_SCALE_V * int(avg_data) / VBAT_ADC_FULL_SCALE


def avg_to_rl(avg_data: int, d2s_gain: int = DEFAULT_D2S_GAIN) -> float:
    """``RL = 610 * AVG_DATA / (1023 * D2S_GAIN)`` Ω（``DET_READ seq=3``）。

    ``D2S_GAIN`` 由 ``0x50 bit2:0`` 决定（默认 ``b100`` = 8）。``d2s_gain <= 0``
    返回 ``nan``，避免除零。
    """
    gain = int(d2s_gain)
    if gain <= 0:
        return float("nan")
    return RL_FACTOR * int(avg_data) / (VBAT_ADC_FULL_SCALE * gain)


def adc_data_from_regs(reg51: int, reg52: int, reg53: int) -> tuple[int, int]:
    """由 ``0x51`` / ``0x52`` / ``0x53`` 原始值解出 ``(ADC_DATA, AVG_DATA)``。

    协议 §9.9：``ADC_DATA = (0x51 bit3:2) * 256 + 0x53``，
    ``AVG_DATA = (0x51 bit1:0) * 256 + 0x52``。
    """
    r51 = int(reg51) & 0xFF
    adc = ((r51 >> 2) & 0x03) * 256 + (int(reg53) & 0xFF)
    avg = (r51 & 0x03) * 256 + (int(reg52) & 0xFF)
    return adc, avg


def f0_within(a_hz: Optional[float], target_hz: float, tolerance: float = 0.05) -> bool:
    """判断实测 F0 是否落在目标值的 ``±tolerance`` 相对误差内（验收标准第 6 条：<5%）。"""
    if a_hz is None or not math.isfinite(a_hz):
        return False
    if target_hz <= 0:
        raise ValueError("目标 F0 必须为正数")
    return abs(a_hz - target_hz) / float(target_hz) <= abs(float(tolerance))
