# -*- coding: utf-8 -*-
"""``awtest.waveform`` 单测：波形生成、整数周期换算、CSV 往返、编辑模型与 SRAM 预算。"""

from __future__ import annotations

import math
import os

import pytest

from awtest.protocol import commands as C
from awtest.waveform import (WaveformDoc, generate, load_csv, periods_for,
                             save_csv, sram_budget, to_bytes, from_bytes)
from awtest.waveform import generator as G

ALL_SHAPES = sorted(G.SHAPES)


# --------------------------------------------------------------------------
# shape 定义
# --------------------------------------------------------------------------


def test_shapes_match_protocol_constants():
    assert G.SHAPES == dict(C.SHAPE_NAMES)
    assert set(G.SHAPES) == set(range(7))
    assert G.SHAPES[0] == "正弦"
    assert G.SHAPES[6] == "点击（单周期衰减）"


def test_shape_code_accepts_name_and_code():
    assert G.shape_code(0) == 0
    assert G.shape_code("正弦") == 0
    assert G.shape_code("点击") == 6
    assert G.shape_code("正弦+下降包络") == 5
    assert G.shape_code("5") == 5
    with pytest.raises(ValueError):
        G.shape_code("不存在的波形")
    with pytest.raises(ValueError):
        G.shape_code(9)


# --------------------------------------------------------------------------
# 整数周期换算
# --------------------------------------------------------------------------


def test_periods_for_is_integer_period():
    n, actual = periods_for(175, 24000)
    assert isinstance(n, int) and n >= 2
    assert n == 138                       # ceil(24000/175) = 138
    assert actual == pytest.approx(24000 / 138)
    assert actual < 175                   # 向上取整 -> 实际频率略低


def test_periods_for_exact_division():
    n, actual = periods_for(1000, 24000)
    assert (n, actual) == (24, 1000.0)
    n, actual = periods_for(12000, 24000)
    assert (n, actual) == (2, 12000.0)


def test_periods_for_never_below_two_samples():
    n, actual = periods_for(30000, 24000)
    assert n == 2
    assert actual == 12000.0


def test_periods_for_rejects_bad_input():
    with pytest.raises(ValueError):
        periods_for(0, 24000)
    with pytest.raises(ValueError):
        periods_for(175, 0)


# --------------------------------------------------------------------------
# 波形生成
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_generate_length_and_range(shape):
    amp, cycles, sr = 100, 3, 24000
    n, _ = periods_for(175, sr)
    samples = generate(shape, 175, amp, cycles, sr)
    expected_len = n if shape == 6 else n * cycles
    assert len(samples) == expected_len
    assert all(-127 <= s <= 127 for s in samples)
    assert all(isinstance(s, int) for s in samples)
    assert max(abs(s) for s in samples) <= amp
    if shape in (0, 1, 2, 3):
        # 无包络的波形峰值应该正好等于给定幅度
        assert max(abs(s) for s in samples) == pytest.approx(amp, abs=1)
    else:
        # 带包络/点击的波形峰值必然低于幅度，但必须明显非零
        assert max(abs(s) for s in samples) > amp // 4


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_generate_respects_amplitude(shape):
    samples = generate(shape, 200, 40, 2, 12000)
    assert max(abs(s) for s in samples) <= 40


def test_generate_sine_is_antisymmetric():
    n, _ = periods_for(1000, 24000)
    samples = generate(0, 1000, 100, 1, 24000)
    assert samples[0] == 0
    assert samples[n // 4] == pytest.approx(100, abs=1)
    for i in range(1, n):
        assert samples[i] == -samples[n - i]


def test_generate_square_and_triangle_symmetry():
    n, _ = periods_for(1000, 24000)          # n = 24
    square = generate(1, 1000, 90, 1, 24000)
    assert square[0] == 90 and square[n // 2] == -90
    assert sum(square) == 0
    tri = generate(2, 1000, 90, 1, 24000)
    assert tri[0] == -90                     # 一周期起点在最负
    assert tri[n // 2] == pytest.approx(90, abs=1)
    assert min(tri) == -90 and max(tri) == pytest.approx(90, abs=1)
    # 三角波关于半周期镜像对称
    for i in range(1, n // 2):
        assert tri[i] == tri[n - i]


def test_generate_saw_is_monotonic_within_period():
    n, _ = periods_for(1000, 24000)
    saw = generate(3, 1000, 80, 1, 24000)
    assert saw[0] == -80
    assert saw == sorted(saw)


def test_generate_envelopes():
    n, _ = periods_for(1000, 24000)
    rise = generate(4, 1000, 100, 1, 24000)
    fall = generate(5, 1000, 100, 1, 24000)
    # 上升包络：第 2 个四分之一周期（相位 90°）峰值应低于第一个
    assert abs(rise[n // 4]) < abs(rise[3 * n // 4])
    assert abs(fall[n // 4]) > abs(fall[3 * n // 4])
    assert max(abs(s) for s in rise) <= 100


def test_generate_click_decays():
    n, _ = periods_for(175, 24000)
    click = generate(6, 175, 100, 5, 24000)
    assert len(click) == n                    # 点击固定单周期，忽略 cycles
    assert abs(click[-1]) < abs(click[n // 4])
    assert abs(click[0]) == 0
    assert max(abs(s) for s in click) <= 100


def test_generate_phase_shifts_waveform():
    a = generate(0, 1000, 100, 1, 24000)
    b = generate(0, 1000, 100, 1, 24000, phase_deg=90)
    assert b[0] == pytest.approx(100, abs=1)
    assert a != b


def test_generate_rejects_bad_amplitude_and_cycles():
    with pytest.raises(ValueError):
        generate(0, 175, 200, 1, 24000)
    with pytest.raises(ValueError):
        generate(0, 175, -1, 1, 24000)
    with pytest.raises(ValueError):
        generate(0, 175, 90, 0, 24000)


# --------------------------------------------------------------------------
# 字节打包
# --------------------------------------------------------------------------


def test_bytes_roundtrip_two_complement():
    samples = [-127, -1, 0, 1, 127, -128]
    data = to_bytes(samples)
    assert data[0] == 0x81
    assert data[-1] == 0x80                     # -128 -> 0x80
    assert from_bytes(data) == samples


def test_bytes_roundtrip_random_waveform():
    samples = generate(0, 175, 90, 4, 24000)
    assert from_bytes(to_bytes(samples)) == samples


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------


def test_csv_roundtrip(tmp_path):
    samples = generate(2, 175, 90, 2, 24000)
    path = str(tmp_path / "wave.csv")
    save_csv(path, samples, sample_rate=24000)
    with open(path, "r", encoding="utf-8") as fh:
        head = fh.readline().strip()
    assert head == "time_ms,amplitude"
    assert load_csv(path) == samples


def test_csv_one_column_without_header(tmp_path):
    path = str(tmp_path / "one.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("0\n10\n-10\n127\n")
    assert load_csv(path) == [0, 10, -10, 127]


def test_csv_two_columns_with_header_and_comments(tmp_path):
    path = str(tmp_path / "two.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 时间(ms), 幅度\n")
        fh.write("time_ms,amplitude\n")
        fh.write("0.0000,5\n")
        fh.write("0.0417,-5\n")
        fh.write("\n")
        fh.write("0.0833,15\n")
    assert load_csv(path) == [5, -5, 15]


def test_csv_clips_out_of_range_and_records_notes(tmp_path):
    path = str(tmp_path / "clip.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("amplitude\n")
        fh.write("0\n")
        fh.write("300\n")          # 越界 -> 127
        fh.write("-300\n")         # 越界 -> -127
        fh.write("这不是数字\n")
    notes: list[str] = []
    samples = load_csv(path, notes)
    assert samples == [0, 127, -127]
    assert any("越界" in n and "裁剪" in n for n in notes)
    assert any("无法解析" in n for n in notes)
    # 模块级也保留了最近一次的说明
    assert any("越界" in n for n in __import__("awtest.waveform", fromlist=["x"]).LAST_LOAD_NOTES)


def test_csv_empty_file_raises(tmp_path):
    path = str(tmp_path / "empty.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("time_ms,amplitude\n")
    with pytest.raises(ValueError):
        load_csv(path)


def test_save_csv_creates_directory(tmp_path):
    path = str(tmp_path / "sub" / "dir" / "w.csv")
    save_csv(path, [1, 2, 3])
    assert os.path.isfile(path)
    assert load_csv(path) == [1, 2, 3]


# --------------------------------------------------------------------------
# 编辑模型
# --------------------------------------------------------------------------


def test_doc_basic_properties():
    doc = WaveformDoc([0, 10, -20, 300])
    assert doc.samples == [0, 10, -20, 127]           # 构造时就裁剪
    assert doc.peak() == 127
    assert doc.duration_ms(24000) == pytest.approx(4 * 1000 / 24000)


def test_undo_redo_at_least_20_steps():
    doc = WaveformDoc([10] * 8)
    original = list(doc.samples)
    for i in range(25):
        doc.offset(1)                        # 每步 +1，25 步后应为 [35]*8
    assert doc.samples == [35] * 8
    assert doc.can_undo()
    undone = 0
    while doc.undo():
        undone += 1
    assert undone >= 20                              # 至少支持 20 步撤销
    assert doc.samples == original
    redone = 0
    while doc.redo():
        redone += 1
    assert redone == undone
    assert doc.samples == [35] * 8
    assert doc.undo() is True
    assert doc.redo() is True
    doc.clear_history()
    assert doc.undo() is False and doc.redo() is False


def test_transforms_scale_offset_invert():
    doc = WaveformDoc([0, 10, -20])
    doc.scale(2.0)
    assert doc.samples == [0, 20, -40]
    doc.offset(5)
    assert doc.samples == [5, 25, -35]
    doc.invert()
    assert doc.samples == [-5, -25, 35]
    doc.undo()
    assert doc.samples == [5, 25, -35]
    # 裁剪边界
    doc2 = WaveformDoc([100, -100])
    doc2.offset(100)
    assert doc2.samples == [127, 0]


def test_transforms_crop_and_mute():
    doc = WaveformDoc(list(range(-5, 5)))
    doc.crop(2, 6)
    assert doc.samples == [-3, -2, -1, 0]
    doc.mute(1, 3)
    assert doc.samples == [-3, 0, 0, 0]
    with pytest.raises(ValueError):
        doc.crop(3, 1)


def test_resample_interpolation_keeps_endpoints():
    doc = WaveformDoc([0, 100, 0, -100])
    doc.resample(7)
    assert len(doc.samples) == 7
    assert doc.samples[0] == 0
    assert doc.samples[-1] == -100
    assert doc.samples[1] == 50
    doc.resample(2)
    assert len(doc.samples) == 2
    assert doc.samples == [0, -100]
    with pytest.raises(ValueError):
        doc.resample(0)


def test_draw_with_brush():
    doc = WaveformDoc([0] * 10)
    doc.draw(5, 77)
    assert doc.samples[5] == 77
    doc.draw(5, 30, brush=2)
    assert doc.samples[3:8] == [30] * 5
    doc.draw(100, 5)                       # 越界忽略
    assert doc.samples == [0, 0, 0, 30, 30, 30, 30, 30, 0, 0]
    doc2 = WaveformDoc([0] * 3)
    doc2.draw(1, 999)                      # 值被裁剪
    assert doc2.samples[1] == 127


def test_draw_auto_undo_can_be_disabled():
    doc = WaveformDoc([0] * 4)
    doc.auto_undo = False
    doc.draw(0, 10)
    doc.draw(1, 20)
    assert doc.can_undo() is False        # 手绘笔画由界面自己压栈
    doc.push_undo()
    doc.draw(2, 30)
    assert doc.undo() is True
    assert doc.samples == [10, 20, 0, 0]


def test_set_samples_does_not_touch_history():
    doc = WaveformDoc([1, 2, 3])
    doc.set_samples([9, 9])
    assert doc.samples == [9, 9]
    assert doc.can_undo() is False


# --------------------------------------------------------------------------
# SRAM 预算
# --------------------------------------------------------------------------


def test_sram_budget_formula_and_default_budget():
    used, avail, overflow = sram_budget(3, 500)
    assert used == 1 + 4 * 3 + 500
    # 本板 base_addr = 0x0800 → 波形库可用 3072 − 2048 = 1024 字节
    # （BASE_ADDR 以下的 2 kB 留给 RTP FIFO；与固件 wave_lib_avail_bytes() 同一公式）
    assert avail == 1024
    assert overflow is False


def test_sram_budget_boundary_exact_and_one_over():
    # 1 条波形：used = 1 + 4 + N，恰好用满 1024 -> N = 1019
    used, avail, overflow = sram_budget(1, 1019, base_addr=0x0800)
    assert (used, avail, overflow) == (1024, 1024, False)
    used, avail, overflow = sram_budget(1, 1020, base_addr=0x0800)
    assert (used, avail, overflow) == (1025, 1024, True)
    # 8 条波形：1 + 32 = 33 字节头开销
    used, avail, overflow = sram_budget(8, 1024 - 33, base_addr=0x0800)
    assert (used, overflow) == (1024, False)
    used, _, overflow = sram_budget(8, 1024 - 32, base_addr=0x0800)
    assert (used, overflow) == (1025, True)
    # 最大 N=127：头块 1+508=509 字节
    used, avail, overflow = sram_budget(127, 1024 - 509, base_addr=0x0800)
    assert (used, avail, overflow) == (1024, 1024, False)


def test_sram_budget_other_base_addr_and_bad_input():
    used, avail, overflow = sram_budget(0, 0, base_addr=0x0400)
    assert (used, avail, overflow) == (1, 2048, False)
    used, avail, _ = sram_budget(-5, -5, base_addr=0x0800)
    assert used == 1                        # 负数被夹成 0
    # base_addr 非法时回落到默认 0x0800
    used, avail, _ = sram_budget(0, 0, base_addr=0x9999)
    assert avail == 1024
