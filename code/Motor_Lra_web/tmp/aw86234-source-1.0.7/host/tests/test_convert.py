# -*- coding: utf-8 -*-
"""参数换算单测（``awtest.waveform.convert``）。

覆盖 `05` 文档 §5.4 与 `03` 文档 §9.7/§9.9 里的全部公式：
``F_PRE ↔ F0``、``F0 = 192000/(H*256+L)``（一次性检测，实测修正基准）、``DRV_LVL → VDD*L/128``、
``DSP 时间 code/48000 ↔ µs``、``VDD = 6.1*AVG/1023``、``RL = 610*AVG/(1023*D2S_GAIN)``。
"""

from __future__ import annotations

import math

import pytest

from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.waveform import convert


# --------------------------------------------------------------------------
# F_PRE <-> F0
# --------------------------------------------------------------------------


def test_f_pre_to_f0_basic():
    assert convert.f_pre_to_f0(240) == pytest.approx(100.0)
    assert convert.f_pre_to_f0(141) == pytest.approx(170.2127, rel=1e-4)
    assert convert.f_pre_to_f0(1) == 24000.0
    with pytest.raises(ValueError):
        convert.f_pre_to_f0(0)


def test_f0_to_f_pre_round_trip():
    assert convert.f0_to_f_pre(100.0) == 240
    assert convert.f0_to_f_pre(170.0) == 141
    # 反算后回代的实际频率落在 ±5% 内（界面要显示实际值）
    for target in (100.0, 170.0, 175.0, 235.0):
        code = convert.f0_to_f_pre(target)
        actual = convert.f_pre_to_f0(code)
        assert convert.f0_within(actual, target, 0.05)


def test_f0_to_f_pre_clamps_or_raises():
    assert convert.f0_to_f_pre(10.0) == 255          # 太低的 F0 -> 上限 255
    assert convert.f0_to_f_pre(100000.0) == 1        # 太高的 F0 -> 下限 1
    with pytest.raises(ValueError):
        convert.f0_to_f_pre(10.0, clamp=False)
    with pytest.raises(ValueError):
        convert.f0_to_f_pre(0)


def test_f0_within_matches_codec_style():
    assert convert.f0_within(175.0, 170.0, 0.05) is True    # 偏差 2.9% -> 通过
    assert convert.f0_within(175.0, 170.0, 0.02) is False   # 同样偏差，收紧到 2% -> 不通过
    assert convert.f0_within(None, 170.0) is False
    with pytest.raises(ValueError):
        convert.f0_within(100.0, 0)


# --------------------------------------------------------------------------
# F0 原始值
# --------------------------------------------------------------------------


def test_f0_from_raw_uses_192000_base_for_oneshot():
    # raw = 0x0B70 = 2928 -> 192000 / 2928（一次性检测的实测修正基准）
    assert convert.raw_to_u16(0x0B, 0x70) == 2928
    assert convert.f0_from_raw(0x0B, 0x70) == pytest.approx(192000 / 2928)
    assert convert.f0_from_raw(0x00, 0xC0) == pytest.approx(1000.0)   # 192 -> 1000 Hz
    assert convert.f0_from_raw(0, 0) is None                          # 除零 -> 无效


def test_f0_from_raw_is_consistent_with_codec():
    resp = K.F0Result(valid=1, mode=0, raw_h=0x00, raw_l=0xC0, f0_hz_x10=10000)
    assert resp.f0_hz == pytest.approx(convert.f0_from_raw(0x00, 0xC0))


def test_cont_f0_uses_384000_base():
    """连续跟踪寄存器 0x26/0x27 仍按手册的 384000。"""
    assert C.F0_RAW_BASE_ONESHOT == 192000.0
    assert C.F0_RAW_BASE_CONT == 384000.0


# --------------------------------------------------------------------------
# 电平 / 增益
# --------------------------------------------------------------------------


def test_drv_lvl_to_voltage():
    assert convert.drv_lvl_to_voltage(64, 5.0) == pytest.approx(2.5)
    assert convert.drv_lvl_to_voltage(127, 5.0) == pytest.approx(5.0 * 127 / 128)
    assert convert.drv_lvl_to_voltage(0, 3.3) == 0.0
    with pytest.raises(ValueError):
        convert.drv_lvl_to_voltage(128, 5.0)


def test_gain_to_ratio():
    assert convert.gain_to_ratio(128) == pytest.approx(1.0)
    assert convert.gain_to_ratio(0) == 0.0
    assert convert.gain_to_ratio(255) == pytest.approx(1.9921875)


# --------------------------------------------------------------------------
# DSP 时间码 <-> 微秒
# --------------------------------------------------------------------------


def test_dsp_time_conversion():
    assert convert.dsp_time_to_us(48000) == pytest.approx(1e6)      # 1 秒
    assert convert.dsp_time_to_us(8) == pytest.approx(166.6667, rel=1e-4)   # START_DLY 默认
    assert convert.dsp_time_to_us(106) == pytest.approx(2208.3333, rel=1e-4)  # DRV_WIDTH 默认
    assert convert.us_to_dsp_time(1000.0) == 48
    assert convert.us_to_dsp_time(166.6667) == 8
    for code in (0, 1, 8, 106, 255):
        assert convert.us_to_dsp_time(convert.dsp_time_to_us(code)) == code
    with pytest.raises(ValueError):
        convert.us_to_dsp_time(-1)


def test_drv_width_must_fit_in_half_period():
    """`05` 文档 §5.4：驱动宽度必须小于 F0 半周期。"""
    f_pre = 141
    half_period_us = 1e6 / convert.f_pre_to_f0(f_pre) / 2.0
    assert convert.dsp_time_to_us(106) < half_period_us
    # F_PRE=80（300 Hz）时默认的 106 就会越界
    half_300 = 1e6 / convert.f_pre_to_f0(80) / 2.0
    assert convert.dsp_time_to_us(106) > half_300


# --------------------------------------------------------------------------
# ADC / 电池电压 / LRA 阻抗
# --------------------------------------------------------------------------


def test_avg_to_vdd():
    assert convert.avg_to_vdd(1023) == pytest.approx(6.1)
    assert convert.avg_to_vdd(0) == 0.0
    assert convert.avg_to_vdd(512) == pytest.approx(6.1 * 512 / 1023)


def test_avg_to_rl():
    assert convert.avg_to_rl(1023, 8) == pytest.approx(610.0 / 8)
    assert convert.avg_to_rl(1023, 1) == pytest.approx(610.0)
    assert convert.avg_to_rl(512, 8) == pytest.approx(610.0 * 512 / (1023 * 8))
    assert math.isnan(convert.avg_to_rl(100, 0))


def test_adc_and_avg_data_split_from_regs():
    # 0x51 bit3:2 = ADC 高 2 位，bit1:0 = AVG 高 2 位
    reg51 = (0b10 << 2) | 0b01         # ADC_H=2, AVG_H=1
    adc, avg = convert.adc_data_from_regs(reg51, 0x34, 0x78)
    assert adc == 2 * 256 + 0x78
    assert avg == 1 * 256 + 0x34
    # 与 codec 的口径一致
    snap = K.Snapshot(reg51=reg51, reg52=0x34, reg53=0x78)
    assert (adc, avg) == (snap.adc_data, snap.avg_data)
