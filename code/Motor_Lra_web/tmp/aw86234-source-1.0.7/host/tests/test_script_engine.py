# -*- coding: utf-8 -*-
"""``awtest.script.engine`` / ``report`` 单测。

**不连真串口**：用 :class:`FakeDevice` 模拟 ``AWTestDevice`` 的所需方法
（协议 §9/§10 的语义尽量贴近真实固件），覆盖正常流程、断言、``expect_error``、
人工读数、``repeat``、中止、事件等待与报告导出。
"""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from awtest import profiles as P
from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.protocol.framing import CHAN_ALL, Frame
from awtest.protocol.transport import DeviceError
from awtest.script import (CSV_HEADER, ScriptEngine, default_report_paths,
                           load_script, parse_script, write_csv, write_markdown)
from awtest.script.report import build_markdown

# --------------------------------------------------------------------------
# 假设备
# --------------------------------------------------------------------------

#: 默认寄存器内容（覆盖脚本里会读到的地址）
DEFAULT_REGS = {
    0x04: 0x08,      # SYSST2：bit3 LDO_OK = 1
    0x18: 141,       # F_PRE
    0x1C: 0x10,      # DRV1_LVL = 16（TRACK_EN=0）
    0x39: 0x08,      # TRG1_BRK = 1
    0x3A: 0x04,      # TRG1_STOP = 1
    0x45: 0x73,
    0x57: 0x23,      # CHIPID_H
    0x69: 0x40,      # CHIPID_L
}


class FakeTransport:
    """只有事件回调的假传输层。"""

    def __init__(self) -> None:
        self.on_event = lambda frame: None
        self.frames: List[Frame] = []

    def emit(self, cmd: int, payload: bytes = b"", chan: int = 0) -> None:
        frame = Frame.event(cmd, payload, chan=chan)
        self.frames.append(frame)
        self.on_event(frame)


class FakeDevice:
    """模拟 ``AWTestDevice`` 的所需子集（不接触任何串口）。"""

    def __init__(self) -> None:
        self.t = FakeTransport()
        self.info = SimpleNamespace(port="COM_FAKE")
        self.version = "0.0-test"
        self.profiles: Dict[int, P.Profile] = {}
        self.modes: Dict[int, int] = {}
        self.d2s_gain = 8
        self.regs: Dict[int, int] = dict(DEFAULT_REGS)
        self.playing = False
        # raw 与 f0_hz_x10 必须自洽：一次性检测基准是 192000（实测修正，见 commands.py）
        #   192000 / 1097 = 175.0 Hz
        self.f0_result = K.F0Result(valid=1, mode=0, raw_h=0x04, raw_l=0x49,
                                    f0_hz_x10=1750)
        self.safety = K.SafetyCfg(play_timeout_ms=2000, max_simul_full_amp=2,
                                  heartbeat_timeout_ms=5000, flags=0)
        self.rtp_params: Optional[K.RtpParams] = None
        self.trig_params: Optional[K.TrigParams] = None
        self.calls: List[tuple] = []
        self.raise_on: Dict[str, DeviceError] = {}

    # ---- 内部 ----

    def _glb_state(self) -> int:
        if not self.playing:
            return C.GLB_STANDBY
        mode = self.modes.get(0, C.MODE_RAM)
        return {C.MODE_RAM: C.GLB_RAM, C.MODE_CONT: C.GLB_CONT,
                C.MODE_RTP: C.GLB_RTP, C.MODE_TRIG: C.GLB_TRIG}.get(mode, C.GLB_RAM)

    def _maybe_raise(self, key: str) -> None:
        exc = self.raise_on.get(key)
        if exc is not None:
            raise exc

    def _full_amp_count(self, mask: int) -> int:
        n = 0
        for ch in range(8):
            if not (mask & (1 << ch)):
                continue
            prof = self.profiles.get(ch)
            if prof is None:
                continue
            if prof.mode == C.MODE_CONT and prof.drv1_lvl > C.FULL_AMP_CONT_DRV1_LVL:
                n += 1
            elif prof.mode in (C.MODE_RAM, C.MODE_RTP) and prof.gain > C.FULL_AMP_RAM_GAIN:
                n += 1
        return n

    def _play_done(self, timeout_ms: int) -> None:
        """播放结束：短超时按"安全超时"(reason=2)上报，否则自然结束。"""
        self.playing = False
        reason = C.PLAY_DONE_SAFETY_TIMEOUT if 0 < timeout_ms < 500 else C.PLAY_DONE_NATURAL
        self.t.emit(C.EVT_PLAY_DONE, K.EvtPlayDone(reason, timeout_ms).encode(), chan=0)

    # ---- 系统 ----

    def ping(self) -> K.PingResp:
        return K.PingResp(1, 1, 2, 3, 1, 1234)

    def get_caps(self) -> K.Caps:
        return K.Caps(8, 200, C.CAPS_EXPECTED_BOARD_A)

    def get_status(self) -> K.Status:
        return K.Status(uptime_ms=1000, safety_state=0, active_mask=0,
                        last_err=C.ERR_SAFETY_TIMEOUT, heartbeat_age_ms=10)

    def get_safety(self) -> K.SafetyCfg:
        return self.safety

    def set_safety(self, cfg: K.SafetyCfg) -> K.SafetyCfg:
        self.safety = cfg
        return cfg

    def clear_errors(self) -> None:
        self.calls.append(("clear_errors",))

    def bus_probe(self, bus_sel: int = 0) -> K.BusProbeResp:
        return K.BusProbeResp(1, 6, 7, 400000, 0, 1, 0x70, 0x01)

    # ---- 通道 ----

    def ch_probe(self, chan: int) -> K.ChProbeResp:
        return K.ChProbeResp(1, 0x23, 0x40, 1)

    def ch_probe_all(self) -> K.ChProbeAllResp:
        entries = [(ch, 0x23, 0x40, 0) for ch in range(8)]
        return K.ChProbeAllResp(0xFF, 8, entries)

    def ch_get_info(self, chan: int) -> K.ChInfo:
        return K.ChInfo(1, 0x23, 0x40, self.modes.get(chan, C.MODE_RAM),
                        int(self.playing), self._glb_state(), 0)

    # ---- 寄存器 ----

    def reg_read(self, chan: int, addr: int, length: int = 1) -> bytes:
        if addr == 0x3F:
            value = self._glb_state()
        else:
            value = self.regs.get(addr, 0)
        return bytes([value]) if length == 1 else bytes((value + i) & 0xFF
                                                        for i in range(length))

    def reg_read_u8(self, chan: int, addr: int) -> int:
        return self.reg_read(chan, addr, 1)[0]

    def reg_write(self, chan: int, addr: int, data) -> None:
        self.regs[addr] = int(data[0]) if not isinstance(data, int) else data & 0xFF

    def reg_update(self, chan: int, addr: int, mask: int, value: int) -> K.RegUpdateResp:
        old = self.reg_read_u8(chan, addr)
        new = (old & ~mask) | (value & mask)
        self.regs[addr] = new
        return K.RegUpdateResp(old, new)

    # ---- 播放 ----

    def set_mode(self, chan: int, mode: int) -> None:
        self.modes[chan] = mode
        self.calls.append(("set_mode", chan, mode))

    def play(self, chan: int, timeout_ms: int = 0, override: bool = False) -> K.PlayResp:
        self._maybe_raise("play")
        actual = timeout_ms or self.safety.play_timeout_ms
        self.playing = True
        self.calls.append(("play", chan, timeout_ms, override))
        self._play_done(actual)
        return K.PlayResp(actual)

    def stop(self, chan: int, immediate: bool = True) -> None:
        self.playing = False
        self.calls.append(("stop", chan, immediate))

    def stop_all(self, immediate: bool = True) -> int:
        self.playing = False
        return 0xFF

    def play_multi(self, chan_mask: int, timeout_ms: int = 0, skew_us: int = 0,
                   override: bool = False) -> K.PlayMultiResp:
        if not override and self._full_amp_count(chan_mask) > self.safety.max_simul_full_amp:
            raise DeviceError(C.CMD_PLAY_MULTI, CHAN_ALL, C.ERR_SAFETY_LIMIT,
                              f"掩码 0x{chan_mask:02X} 中有 "
                              f"{self._full_amp_count(chan_mask)} 路满幅，超过 "
                              f"{self.safety.max_simul_full_amp}")
        return K.PlayMultiResp(chan_mask, skew_us)

    def trig_park(self) -> None:
        self.calls.append(("trig_park",))

    def trig_pulse(self, chan_mask: int, width_us: int = 20, count: int = 1,
                   period_ms: int = 100, initial_delay_us: int = 0) -> int:
        return chan_mask

    def trig_level(self, chan_mask: int, level: int, hold_ms: int) -> None:
        self.calls.append(("trig_level", chan_mask, level, hold_ms))

    def trig_pattern(self, chan_mask: int, on_us: int = 20, off_us: int = 20,
                     count: int = 10, period_ms: int = 100) -> None:
        self.calls.append(("trig_pattern", chan_mask))

    def get_play_state(self, chan: int) -> K.PlayState:
        return K.PlayState(int(self.playing), self._glb_state(), 0)

    # ---- 参数 ----

    def set_profile(self, chan: int, profile: P.Profile) -> K.SetProfileResp:
        if chan == CHAN_ALL:
            for ch in range(8):
                self.profiles[ch] = profile.copy()
        else:
            self.profiles[chan] = profile.copy()
        self.modes[chan] = profile.mode
        # 让 0x1C / 0x18 的回读与刚下发的 Profile 一致（模拟固件真的写进去了）
        self.regs[0x1C] = (self.regs.get(0x1C, 0) & 0x80) | (profile.drv1_lvl & 0x7F)
        self.regs[0x18] = profile.f_pre & 0xFF
        return K.SetProfileResp(0, 0)

    def get_profile(self, chan: int) -> P.Profile:
        return self.profiles.get(chan, P.Profile())

    def set_gain(self, chan: int, gain: int) -> None:
        self.calls.append(("set_gain", chan, gain))

    def set_vbat(self, chan: int, mode: int, ref: int) -> None:
        self.calls.append(("set_vbat", chan, mode, ref))

    def set_cont_live(self, chan: int, drv1_lvl: int, drv2_lvl: int,
                      noresp: bool = True) -> None:
        self.calls.append(("set_cont_live", chan, drv1_lvl, drv2_lvl))

    def set_ram_params(self, chan: int, params: K.RamParams) -> None:
        self.calls.append(("set_ram_params", chan))

    def set_cont_params(self, chan: int, params: K.ContParams) -> None:
        self.calls.append(("set_cont_params", chan, params.f_pre))

    def set_rtp_params(self, chan: int, params: K.RtpParams) -> None:
        if params.wavdat_mode == C.RATE_48K:
            raise DeviceError(C.CMD_SET_RTP_PARAMS, chan, C.ERR_UNSUPPORTED,
                              "本板 I2C 只到 400 kHz，48K RTP 不可用")
        self.rtp_params = params

    def rtp_data(self, chan: int, offset: int, data: bytes) -> None:
        assert 1 <= len(data) <= 64
        self.calls.append(("rtp_data", chan, offset, len(data)))

    def set_trig_params(self, chan: int, params: K.TrigParams) -> None:
        if not params.trig1_brk or not params.trig1_stop:
            raise DeviceError(C.CMD_SET_TRIG_PARAMS, chan, C.ERR_BAD_PARAM,
                              "TRG1_BRK 与 TRG1_STOP 必须为 1（安全要求，不可关闭）")
        self.trig_params = params
        self.regs[0x3A] |= 0x04
        self.regs[0x39] |= 0x08

    def set_default_profile(self, chan: int) -> None:
        self.calls.append(("set_default_profile", chan))

    # ---- 波形 ----

    def wave_begin(self, chan: int, sample_rate_code: int, wave_count: int,
                   overwrite: bool = True) -> K.WaveBeginResp:
        return K.WaveBeginResp(1, 2043, 2048)

    def wave_add(self, chan: int, wave_id: int, offset: int, total_samples: int,
                 samples: bytes, last: bool = False) -> K.WaveAddResp:
        assert len(samples) <= C.WAVE_MAX_CHUNK_SAMPLES
        self.calls.append(("wave_add", chan, wave_id, offset, len(samples), last))
        return K.WaveAddResp(wave_id, offset)

    def wave_commit(self, chan: int, lib_version: int = 1) -> K.WaveCommitResp:
        return K.WaveCommitResp(0, 1000, 0x1234, 0x0800,
                                [K.WaveLibEntry(1, 0, 999)])

    def wave_gen(self, chan: int, wave_id: int, shape: int, freq_hz: int,
                 amplitude: int, cycles: int, sample_rate_code: int) -> K.WaveGenResp:
        from awtest.waveform import generate, periods_for
        n, _ = periods_for(freq_hz, C.RATE_HZ[sample_rate_code])
        return K.WaveGenResp(wave_id, n * (1 if shape == 6 else cycles))

    def wave_queue(self, chan: int, ids) -> None:
        self.calls.append(("wave_queue", chan, list(ids)))

    def wave_get_library(self, chan: int) -> K.WaveLibrary:
        return K.WaveLibrary(1, 1000, 0x0800, [K.WaveLibEntry(1, 0, 999)])

    def wave_readback(self, chan: int, wave_id: int, offset: int,
                      length: int) -> K.WaveReadbackResp:
        return K.WaveReadbackResp(wave_id, offset, bytes(length))

    def wave_abort(self, chan: int) -> None:
        pass

    def wave_erase(self, chan: int) -> None:
        pass

    # ---- 测量 ----

    def f0_measure(self, chan: int, timeout_ms: int = 500, mode: int = 0,
                   poll_s: float = 0.1) -> K.F0Result:
        return self.f0_result

    def f0_start(self, chan: int, timeout_ms: int = 500, mode: int = 0) -> bool:
        return True

    def f0_result(self, chan: int) -> K.F0Result:
        return self.f0_result

    def det_read(self, chan: int, seq: int) -> K.DetReadResp:
        if seq == C.DET_SEQ_VBAT:
            return K.DetReadResp(adc_data=680, avg_data=680, value_x100=405,
                                 valid=1, seq_echo=seq)
        return K.DetReadResp(adc_data=200, avg_data=140, value_x100=1043,
                             valid=1, seq_echo=seq)

    def snapshot(self, chan: int) -> K.Snapshot:
        return K.Snapshot(1, 0x23, 0x40, self.modes.get(chan, C.MODE_RAM),
                          int(self.playing), self._glb_state(), 0x00, 0x08,
                          0x08, 0x92, 0x00, 0x00, 0x00, 0x8C, 0x00)

    # ---- 扫描 ----

    def sweep_amp(self, chan: int, mode: int, from_lvl: int, to_lvl: int, step: int,
                  dwell_ms: int = 200, settle_ms: int = 100) -> K.SweepResp:
        total = abs(to_lvl - from_lvl) // step + 1
        self.t.emit(C.EVT_SWEEP_DONE, K.EvtSweepDone(total, 0).encode(), chan=chan)
        return K.SweepResp(1, total)

    def sweep_freq(self, chan: int, from_hz: int, to_hz: int, step_hz: int,
                   dwell_ms: int = 200) -> K.SweepResp:
        return K.SweepResp(1, abs(to_hz - from_hz) // step_hz + 1)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


@pytest.fixture()
def device() -> FakeDevice:
    return FakeDevice()


def _script(steps: List[Dict[str, Any]], name: str = "测试脚本") -> Any:
    return parse_script({"name": name, "matrix_ids": ["T1"], "steps": steps})


def _run(device: FakeDevice, steps: List[Dict[str, Any]], **kwargs):
    engine = ScriptEngine(device, _script(steps), **kwargs)
    return engine, engine.run()


# --------------------------------------------------------------------------
# 正常流程
# --------------------------------------------------------------------------


def test_normal_flow_all_steps_pass(device):
    steps = [
        {"op": "log", "text": "开始"},
        {"op": "set_mode", "chan": 0, "mode": "CONT"},
        {"op": "reg_read", "chan": 0, "addr": "0x1C"},
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x10"},
        {"op": "snapshot", "chan": 0},
        {"op": "det_read", "chan": 0, "seq": 3},
        {"op": "stop", "chan": 0},
    ]
    seen: List[Any] = []
    logs: List[str] = []
    engine, run = _run(device, steps, on_step=seen.append, on_log=logs.append)

    assert run.status == "pass"
    assert run.total == len(steps)
    assert run.failed == 0 and run.skipped == 0
    assert run.passed == len(steps)
    assert [s.index for s in run.steps] == list(range(1, len(steps) + 1))
    assert all(s.status == "pass" for s in run.steps)
    assert all(s.elapsed_ms >= 0 for s in run.steps)
    assert len(seen) == len(steps)
    assert run.started_at and run.finished_at
    assert run.port == "COM_FAKE"
    assert run.log and any("开始" in line for line in logs)
    assert run.steps[3].expected and run.steps[3].actual == "0x10"
    assert run.steps[0].matrix_id == "T1"


def test_matrix_id_can_be_overridden_per_step(device):
    steps = [{"op": "log", "text": "x", "matrix_id": "A9"}]
    _engine, run = _run(device, steps)
    assert run.steps[0].matrix_id == "A9"


# --------------------------------------------------------------------------
# 断言
# --------------------------------------------------------------------------


def test_assert_read_pass_and_fail(device):
    steps = [
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x10"},
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x20"},
    ]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"
    assert run.steps[1].status == "fail"
    assert "不符" in run.steps[1].note
    assert run.status == "fail"


def test_assert_read_mask_and_list_expect(device):
    steps = [
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "mask": 0x7F, "expect": 0x10},
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": ["0x11", "0x10"]},
    ]
    _engine, run = _run(device, steps)
    assert [s.status for s in run.steps] == ["pass", "pass"]


def test_assert_equals_and_range_with_manual_value(device):
    steps = [
        {"op": "prompt", "message": "Vpp?", "field": "vpp", "unit": "V"},
        {"op": "assert_equals", "field": "vpp", "expect": 1.5, "tolerance": 0.01},
        {"op": "assert_range", "field": "vpp", "min": 1.0, "max": 2.0, "unit": "V"},
        {"op": "assert_range", "field": "vpp", "min": 3.0, "max": 4.0},
    ]
    engine = ScriptEngine(device, _script(steps),
                          prompt_handler=lambda m, f, u: "1.5")
    run = engine.run()
    assert run.manual["vpp"] == "1.5"
    assert [s.status for s in run.steps] == ["pass", "pass", "pass", "fail"]
    assert "超出区间" in run.steps[3].note


def test_assert_equals_uses_previous_actual(device):
    steps = [
        {"op": "det_read", "chan": 0, "seq": 3},
        {"op": "assert_equals", "expect": 10.43, "tolerance": 0.01, "previous": True},
    ]
    _engine, run = _run(device, steps)
    assert run.steps[1].status == "pass"
    assert "上一步实测值" in run.steps[1].note


def test_assert_range_without_value_is_skip(device):
    steps = [{"op": "assert_range", "field": "不存在的字段", "min": 1, "max": 2}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "skip"
    assert "无法取得被测值" in run.steps[0].note


# --------------------------------------------------------------------------
# expect_error
# --------------------------------------------------------------------------


def test_expect_error_hit_marks_pass(device):
    sample = P.Profile()
    sample.mode = C.MODE_CONT
    sample.drv1_lvl = 127
    for ch in range(8):
        device.profiles[ch] = sample
    steps = [
        {"op": "play_multi", "chan_mask": 255, "expect_error": "ERR_SAFETY_LIMIT"},
    ]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"
    assert "按预期收到错误应答" in run.steps[0].note
    assert "ERR_SAFETY_LIMIT" in run.steps[0].actual


def test_expect_error_numeric_and_true(device):
    device.raise_on["play"] = DeviceError(C.CMD_PLAY, 0, C.ERR_SAFETY_TIMEOUT, "超时")
    steps = [
        {"op": "play", "chan": 0, "expect_error": 0x11},
        {"op": "play", "chan": 0, "expect_error": True},
        {"op": "play", "chan": 0, "expect_error": "ERR_UNSUPPORTED"},
    ]
    _engine, run = _run(device, steps)
    assert [s.status for s in run.steps] == ["pass", "pass", "fail"]
    assert "期望 ERR_UNSUPPORTED" in run.steps[2].note or \
        "错误码不符" in run.steps[2].note


def test_device_error_without_expect_error_is_fail(device):
    device.raise_on["play"] = DeviceError(C.CMD_PLAY, 0, C.ERR_BUSY, "通道忙")
    _engine, run = _run(device, [{"op": "play", "chan": 0}])
    assert run.steps[0].status == "fail"
    assert "ERR_BUSY" in run.steps[0].actual
    assert run.status == "fail"


def test_assert_read_can_expect_error_name(device):
    """断言步把 expect 写成 ERR_xxx 时，错误应答按期望比对。"""
    def boom(chan, addr, length=1):
        raise DeviceError(C.CMD_REG_READ, chan, C.ERR_NOT_PRESENT, "0x5A 无 ACK")

    device.reg_read = boom
    steps = [{"op": "assert_read", "chan": 7, "addr": "0x57", "expect": "ERR_NOT_PRESENT"}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------


def test_prompt_without_handler_is_skipped(device):
    steps = [{"op": "prompt", "message": "请填 Vpp", "field": "vpp", "unit": "V"}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "skip"
    assert "prompt_handler" in run.steps[0].note
    assert run.manual == {}
    assert run.status == "pass"          # 跳过不算失败


def test_prompt_with_handler_records_manual(device):
    steps = [
        {"op": "prompt", "message": "请填 Vpp", "field": "vpp", "unit": "V"},
        {"op": "prompt", "message": "请填电流", "field": "电流", "unit": "A"},
        {"op": "prompt", "message": "空着不填", "field": "empty", "unit": ""},
    ]
    answers = {"vpp": "1.23", "电流": "0.45", "empty": ""}
    engine = ScriptEngine(device, _script(steps),
                          prompt_handler=lambda m, f, u: answers.get(f))
    run = engine.run()
    assert run.manual == {"vpp": "1.23", "电流": "0.45"}
    assert run.steps[0].actual == "1.23 V"
    assert run.steps[1].actual == "0.45 A"
    assert [s.status for s in run.steps] == ["pass", "pass", "skip"]


def test_prompt_handler_returning_none_is_skip(device):
    engine = ScriptEngine(device,
                          _script([{"op": "prompt", "message": "?", "field": "f"}]),
                          prompt_handler=lambda m, f, u: None)
    run = engine.run()
    assert run.steps[0].status == "skip"


# --------------------------------------------------------------------------
# 循环
# --------------------------------------------------------------------------


def test_repeat_executes_nested_steps(device):
    steps = [
        {"op": "repeat", "count": 3, "steps": [
            {"op": "reg_read", "chan": 0, "addr": "0x1C"},
            {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x10"},
        ]},
    ]
    _engine, run = _run(device, steps)
    assert run.total == 6
    assert run.status == "pass"
    assert [s.index for s in run.steps] == [1, 2, 3, 4, 5, 6]
    assert all("第 1/3 次重复" in s.note or "第 2/3 次重复" in s.note
               or "第 3/3 次重复" in s.note for s in run.steps)


def test_loop_is_alias_of_repeat(device):
    steps = [{"op": "loop", "count": 2, "steps": [{"op": "log", "text": "x"}]}]
    _engine, run = _run(device, steps)
    assert run.total == 2


# --------------------------------------------------------------------------
# 中止与暂停
# --------------------------------------------------------------------------


def test_abort_from_on_step_stops_run(device):
    steps = [
        {"op": "log", "text": "1"},
        {"op": "log", "text": "2"},
        {"op": "log", "text": "3"},
        {"op": "log", "text": "4"},
    ]
    engine = ScriptEngine(device, _script(steps))
    engine.on_step = lambda res: engine.abort() if res.index >= 2 else None
    run = engine.run()
    assert run.status == "abort"
    assert run.total == 2


def test_should_abort_callback_is_honoured(device):
    steps = [{"op": "log", "text": str(i)} for i in range(5)]
    state = {"steps": 0}

    def on_step(res) -> None:
        state["steps"] += 1

    def should_abort() -> bool:
        return state["steps"] >= 2          # 界面上按下"中止"按钮的效果

    engine = ScriptEngine(device, _script(steps), on_step=on_step,
                          should_abort=should_abort)
    run = engine.run()
    assert run.status == "abort"
    assert run.total == 2


def test_abort_interrupts_long_wait_within_100ms(device):
    steps = [
        {"op": "log", "text": "开始"},
        {"op": "wait", "ms": 2000},
        {"op": "log", "text": "不该执行到这里"},
    ]
    engine = ScriptEngine(device, _script(steps))
    timer = threading.Timer(0.1, engine.abort)
    timer.start()
    started = time.perf_counter()
    run = engine.run()
    elapsed = time.perf_counter() - started
    timer.cancel()
    assert run.status == "abort"
    assert elapsed < 0.5, f"中止响应过慢：{elapsed:.3f}s"
    assert run.total == 1


def test_pause_gate_blocks_until_resume(device):
    steps = [{"op": "log", "text": "1"}, {"op": "log", "text": "2"}]
    engine = ScriptEngine(device, _script(steps))
    holder: List[Any] = []
    engine.pause()
    thread = threading.Thread(target=lambda: holder.append(engine.run()))
    thread.start()
    time.sleep(0.2)
    assert thread.is_alive() and not holder
    engine.resume()
    thread.join(3)
    assert not thread.is_alive()
    assert holder and holder[0].status == "pass"


def test_external_pause_gate_is_called(device):
    calls = {"n": 0}

    def gate() -> None:
        calls["n"] += 1

    engine = ScriptEngine(device, _script([{"op": "log", "text": "x"}]),
                          pause_gate=gate)
    engine.run()
    assert calls["n"] >= 1


# --------------------------------------------------------------------------
# 事件与测量
# --------------------------------------------------------------------------


def test_wait_play_done_uses_event(device):
    steps = [
        {"op": "play", "chan": 0, "timeout_ms": 2000},
        {"op": "wait_play_done", "chan": 0, "timeout_ms": 2000, "expect_reason": 0},
    ]
    _engine, run = _run(device, steps)
    assert [s.status for s in run.steps] == ["pass", "pass"]
    assert "EVT_PLAY_DONE" in run.steps[1].actual
    assert any("EVT_PLAY_DONE" in line for line in run.log)


def test_wait_play_done_reports_safety_timeout(device):
    steps = [
        {"op": "play", "chan": 0, "timeout_ms": 200},
        {"op": "wait_play_done", "chan": 0, "timeout_ms": 2000, "expect_reason": 2},
        {"op": "wait_play_done", "chan": 0, "timeout_ms": 2000, "expect_reason": 0},
    ]
    _engine, run = _run(device, steps)
    assert run.steps[1].status == "pass"
    assert "安全超时" in run.steps[1].actual
    # 第二个等待拿不到新事件 -> 退化为轮询/跳过，不能误判为通过
    assert run.steps[2].status in ("fail", "skip")


def test_wait_event_safety_trip(device):
    def emit_trip() -> None:
        device.t.emit(C.EVT_SAFETY_TRIP,
                      K.EvtSafetyTrip(C.TRIP_TOO_MANY_FULL_AMP, 0xFF, 0).encode(), chan=0)

    steps = [
        {"op": "log", "text": "触发"},
        {"op": "wait_event", "event": "EVT_SAFETY_TRIP", "reason": 2, "timeout_ms": 500},
    ]
    engine = ScriptEngine(device, _script(steps))
    engine.on_step = lambda res: emit_trip() if res.op == "log" else None
    run = engine.run()
    assert run.steps[1].status == "pass"

    engine2 = ScriptEngine(device, _script([{"op": "wait_event",
                                             "event": "EVT_SAFETY_TRIP",
                                             "timeout_ms": 100}]))
    run2 = engine2.run()
    assert run2.steps[0].status == "fail"
    assert "超时" in run2.steps[0].note


def test_wait_event_unknown_name_fails(device):
    _engine, run = _run(device, [{"op": "wait_event", "event": "EVT_不存在"}])
    assert run.steps[0].status == "fail"
    assert "事件表" in run.steps[0].note


def test_f0_valid_and_invalid(device):
    steps = [{"op": "f0", "chan": 0, "timeout_ms": 300, "min_hz": 100, "max_hz": 300}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"
    assert "175" in run.steps[0].actual

    device.f0_result = K.F0Result(valid=0, mode=0, raw_h=0, raw_l=0, f0_hz_x10=0xFFFF)
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "fail"
    assert "C-1" in run.steps[0].note            # 明确诊断，不给假数据

    steps2 = [{"op": "f0", "chan": 0, "expect_valid": False}]
    _engine, run = _run(device, steps2)
    assert run.steps[0].status == "pass"


def test_f0_out_of_range_fails(device):
    _engine, run = _run(device, [{"op": "f0", "chan": 0, "min_hz": 300}])
    assert run.steps[0].status == "fail"
    assert "低于下限" in run.steps[0].note


def test_det_read_values_and_range(device):
    steps = [
        {"op": "det_read", "chan": 0, "seq": 3, "min": 6, "max": 13.6},
        {"op": "det_read", "chan": 0, "seq": 0},
        {"op": "det_read", "chan": 0, "seq": 3, "min": 100},
    ]
    _engine, run = _run(device, steps)
    assert [s.status for s in run.steps] == ["pass", "pass", "fail"]
    assert run.steps[0].actual.endswith("Ω")
    assert run.steps[1].actual.endswith("V")


def test_record_snapshot_content(device):
    device.playing = True
    device.modes[0] = C.MODE_CONT
    _engine, run = _run(device, [{"op": "record", "chan": 0}])
    actual = run.steps[0].actual
    assert "CHIPID=0x2340" in actual
    assert "GLB_STATE=CONT" in actual

    _engine, run = _run(device, [{"op": "record", "chan": 0, "expect_standby": True}])
    assert run.steps[0].status == "fail"


def test_sweep_amp_waits_for_done_event(device):
    steps = [{"op": "sweep_amp", "chan": 0, "mode": 0, "from": 16, "to": 112,
              "step": 16, "wait_ms": 500}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"
    assert "7 步" in run.steps[0].actual
    assert "扫描完成" in run.steps[0].note


def test_set_profile_invalid_field_is_reported_not_raised(device):
    steps = [{"op": "set_profile", "chan": 0, "params": {"drv1_lvl": 200}}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "fail"
    assert "Profile 校验未通过" in run.steps[0].note


def test_set_profile_applies_and_reports_changes(device):
    steps = [{"op": "set_profile", "chan": 0,
              "params": {"mode": 2, "drv1_lvl": 48, "track_en": 0}}]
    _engine, run = _run(device, steps)
    assert run.steps[0].status == "pass"
    assert device.profiles[0].drv1_lvl == 48
    assert device.profiles[0].mode == C.MODE_CONT


def test_rtp_48k_error_and_trig_safety_bits(device):
    steps = [
        {"op": "set_rtp_params", "chan": 0, "wavdat_mode": 1,
         "expect_error": "ERR_UNSUPPORTED"},
        {"op": "set_trig_params", "chan": 0, "trig1_stop": 0,
         "expect_error": "ERR_BAD_PARAM"},
        {"op": "set_trig_params", "chan": 0, "trig1_stop": 1, "trig1_brk": 1},
        {"op": "assert_read", "chan": 0, "addr": "0x3A", "mask": 4, "expect": 4},
    ]
    _engine, run = _run(device, steps)
    assert [s.status for s in run.steps] == ["pass", "pass", "pass", "pass"]


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def _sample_run(device) -> Any:
    steps = [
        {"op": "unused"},
    ]
    steps = [
        {"op": "set_mode", "chan": 0, "mode": "CONT", "matrix_id": "A9"},
        {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x20"},
        {"op": "prompt", "message": "请填 Vpp", "field": "vpp", "unit": "V"},
    ]
    engine = ScriptEngine(device, _script(steps, name="报告测试"),
                          prompt_handler=lambda m, f, u: "1.23")
    return engine.run()


def test_write_csv_header_and_rows(device, tmp_path):
    run = _sample_run(device)
    path = str(tmp_path / "reports" / "r.csv")
    assert write_csv(run, path) == path
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == CSV_HEADER
    assert len(rows) == 1 + run.total
    assert rows[0][0] == "step_index" and rows[0][-1] == "note"
    # 第一行：set_mode 通过
    row = rows[1]
    assert [row[0], row[1], row[2], row[7]] == ["1", "A9", "set_mode", "pass"]
    assert '"chan"' in row[4]                       # params 列是 JSON 摘要
    assert float(row[8]) >= 0
    # 第二行：断言失败
    assert run.steps[1].status == "fail"
    assert rows[2][7] == "fail"
    assert rows[3][2] == "prompt" and rows[3][7] == "pass"
    assert run.manual["vpp"] == "1.23"


def test_write_markdown_structure(device, tmp_path):
    run = _sample_run(device)
    path = str(tmp_path / "r.md")
    assert write_markdown(run, path) == path
    text = open(path, encoding="utf-8").read()
    for section in ("# 测试报告", "## 关键实测值", "## 汇总", "## 详细步骤", "## 结论"):
        assert section in text
    assert "报告测试" in text
    assert "A9" in text
    assert "vpp" in text and "1.23" in text          # 人工读数进关键实测值表
    assert "测试日期" in text and "板号" in text       # meta 补入
    assert "| 步骤号 | 操作 | 通道 | 参数 | 期望 | 实际 | 判定 | 耗时(ms) | 备注 |" in text
    assert "| 步骤总数 | 3 |" in text
    assert "失败" in text


def test_markdown_includes_meta_and_skipped_section(device):
    run = _sample_run(device)
    run.manual.clear()
    steps_skip = _script([{"op": "prompt", "message": "?", "field": "f"}])
    engine = ScriptEngine(device, steps_skip)
    run = engine.run()
    text = build_markdown(run, {"测试日期": "2026-09-30", "板号": "A-V1.0",
                                "固件版本": "1.2.3", "电源": "5.0 V / J10",
                                "自定义项": "x"})
    assert "2026-09-30" in text and "A-V1.0" in text and "1.2.3" in text
    assert "自定义项" in text
    assert "跳过项说明" in text


def test_markdown_abort_and_pass_conclusions(device):
    run = _sample_run(device)
    run.steps[1].status = "pass"
    run.status = "pass"
    text = build_markdown(run)
    assert "判为通过" in text
    run.status = "abort"
    run.steps[1].status = "pass"
    text = build_markdown(run)
    assert "被用户中止" in text


def test_default_report_paths():
    csv_path, md_path = default_report_paths("smoke", "reports")
    assert csv_path.startswith(os.path.join("reports", "smoke_"))
    assert csv_path.endswith(".csv") and md_path.endswith(".md")
    assert os.path.splitext(csv_path)[0] == os.path.splitext(md_path)[0]


# --------------------------------------------------------------------------
# 预设脚本端到端（对着假设备跑）
# --------------------------------------------------------------------------


def _preset(name_prefix: str):
    from awtest.script import list_preset_scripts
    for _name, path in list_preset_scripts():
        if os.path.basename(path).startswith(name_prefix):
            return load_script(path)
    raise AssertionError(f"找不到预设脚本 {name_prefix}")


def test_preset_smoke_script_runs_end_to_end(device):
    engine = ScriptEngine(device, _preset("01"))
    run = engine.run()
    assert run.status == "pass", [(s.index, s.op, s.status, s.note)
                                  for s in run.steps if s.status == "fail"]
    assert run.failed == 0
    assert run.total >= 20
    assert run.script.matrix_ids == ["A2", "A4", "A5"]


def test_preset_safety_script_runs_end_to_end(device):
    engine = ScriptEngine(device, _preset("04"))
    run = engine.run()
    assert run.status == "pass", [(s.index, s.op, s.status, s.note)
                                  for s in run.steps if s.status == "fail"]
    ops = [s.op for s in run.steps]
    assert "play_multi" in ops and "wait_play_done" in ops


def test_preset_streaming_script_runs_end_to_end(device):
    engine = ScriptEngine(device, _preset("05"))
    run = engine.run()
    assert run.status == "pass", [(s.index, s.op, s.status, s.note)
                                  for s in run.steps if s.status == "fail"]
    assert device.rtp_params is not None
    assert any(call[0] == "rtp_data" for call in device.calls)


def test_preset_amplitude_script_runs_end_to_end(device):
    """A9 预设：逐档 DRV1_LVL + 回读核对 + SWEEP_AMP（prompt 无人填 -> 跳过）。"""
    engine = ScriptEngine(device, _preset("02"))
    run = engine.run()
    assert run.status == "pass", [(s.index, s.op, s.status, s.note)
                                  for s in run.steps if s.status == "fail"]
    assert run.skipped >= 5                       # 5 个 prompt 全部跳过
    assert any(s.op == "sweep_amp" for s in run.steps)


def test_preset_channel_mapping_script_runs_end_to_end(device):
    """C1 预设：8 路分别下发不同幅度/频率并逐个回读核对。"""
    engine = ScriptEngine(device, _preset("03"))
    run = engine.run()
    assert run.status == "pass", [(s.index, s.op, s.status, s.note)
                                  for s in run.steps if s.status == "fail"]
    assert run.total > 70
    assert device.profiles[7].drv1_lvl == 96      # CH7 的幅度档位


def test_preset_reports_can_be_written(device, tmp_path):
    name, path = next((n, p) for n, p in
                      __import__("awtest.script", fromlist=["x"]).list_preset_scripts()
                      if os.path.basename(p).startswith("01"))
    engine = ScriptEngine(device, load_script(path))
    run = engine.run()
    csv_path = str(tmp_path / "p.csv")
    md_path = str(tmp_path / "p.md")
    write_csv(run, csv_path)
    write_markdown(run, md_path, {"测试日期": "2026-09-30", "板号": "A-V1.0"})
    assert os.path.getsize(csv_path) > 0 and os.path.getsize(md_path) > 0
    assert name
    rows = open(csv_path, encoding="utf-8").read().splitlines()
    assert len(rows) == run.total + 1
    data = json.loads(json.dumps({"ok": True}))
    assert data["ok"] is True
