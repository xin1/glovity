# -*- coding: utf-8 -*-
"""设备高层 API 的端到端测试（用行为级假设备，不需要真实硬件）。

覆盖：连接流程、通道探测、Profile 下发→回读→比对、安全限制、
CH5/6/7 联动、波形下发与回读校验、F0、DET_READ、SNAPSHOT、整片 dump。
"""

from __future__ import annotations

import pytest

from awtest import profiles as P
from awtest.device import AWTestDevice
from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.protocol.transport import DeviceError

from fake_device import FakeDeviceTransport


def make_device(**kw) -> AWTestDevice:
    dev = AWTestDevice(FakeDeviceTransport(**kw))
    dev.t.open("FAKE")
    dev.t.enter_binary_mode()
    dev.info.caps = dev.get_caps()
    dev.info.ping = dev.ping()
    dev.info.status = dev.get_status()
    dev.info.bus = dev.bus_probe()
    dev.info.channels = dev.ch_probe_all()
    return dev


# --------------------------------------------------------------------------
# 连接与探测
# --------------------------------------------------------------------------


def test_connect_flow_returns_info():
    dev = AWTestDevice(FakeDeviceTransport())
    info = dev.connect("FAKE")
    assert info.ping is not None and info.ping.proto_ver == 1
    assert info.caps.channel_count == 8
    assert info.bus is not None and info.bus.sel_used == 1
    assert info.channels.present_mask == 0xFF
    assert info.ping.fw_version == "1.0.0"
    assert "8 通道" in info.summary()


def test_caps_expected_for_board_a():
    dev = make_device()
    caps = dev.get_caps()
    assert caps.caps & C.CAP_SHARED_TRIG_567
    assert not (caps.caps & C.CAP_RTP_48K)
    assert not (caps.caps & C.CAP_MOTOR_POWER_GPIO)
    assert not (caps.caps & C.CAP_RSTN_GPIO)
    report = caps.cap_report()
    assert len(report) == 20
    assert any(bit == C.CAP_TACTILE for bit, *_ in report)
    # Legacy simulator advertises neither of the optional v1.0.1 additions.
    assert not caps.has(C.CAP_TIMED_PLAY)
    assert not caps.has(C.CAP_BOOT_TRIGGER)
    assert any(bit == C.CAP_RTP_48K and not ok for bit, _n, _d, ok in report)


def test_ch_probe_all_and_chipid():
    dev = make_device()
    resp = dev.ch_probe_all()
    assert resp.present_mask == 0xFF
    assert resp.chipid_of(3) == 0x2340
    one = dev.ch_probe(0)
    assert one.present == 1 and one.chipid_ok and one.vreg_ok == 1


def test_partial_present_mask():
    dev = make_device(present_mask=0x0F)
    resp = dev.ch_probe_all()
    assert resp.present_mask == 0x0F
    assert dev.ch_probe(5).present == 0


# --------------------------------------------------------------------------
# Profile：下发 → 回读 → 比对
# --------------------------------------------------------------------------


def test_profile_roundtrip_verify_success():
    dev = make_device()
    prof = P.preset_cont_f0()
    diff = dev.set_profile_verify(0, prof)
    assert diff == []
    back = dev.get_profile(0)
    assert back.drv1_lvl == prof.drv1_lvl
    assert abs(back.f0_hz - prof.f0_hz) < 0.5


def test_profile_rejected_locally_before_sending():
    dev = make_device()
    prof = P.Profile()
    prof.drv1_lvl = 200          # 越界
    with pytest.raises(DeviceError) as ei:
        dev.set_profile(0, prof)
    assert ei.value.errcode == C.ERR_BAD_PARAM


def test_profile_trig_safety_bits_forced_on():
    dev = make_device()
    prof = P.preset_trig_rising()
    dev.set_profile(0, prof)
    back = dev.get_profile(0)
    assert back.trig1_stop == 1 and back.trig1_brk == 1
    assert back.trig1_mode == 3 and back.intn_pin == 0


def test_set_trig_params_refuses_unsafe_bits():
    dev = make_device()
    tp = K.TrigParams()
    tp.trig1_stop = 0
    with pytest.raises(DeviceError) as ei:
        dev.set_trig_params(0, tp)
    assert ei.value.errcode == C.ERR_BAD_PARAM
    assert "TRG1_STOP" in str(ei.value)


# --------------------------------------------------------------------------
# 安全
# --------------------------------------------------------------------------


def test_safety_check_rejects_too_many_full_amplitude():
    dev = make_device()
    for ch in range(4):
        prof = dev.get_profile(ch)
        prof.mode = C.MODE_CONT
        prof.drv1_lvl = 127          # > 96 → 满幅
        dev.set_profile(ch, prof)
    ok, why = dev.safety_check(0x0F)
    assert not ok and "满幅" in why
    assert dev.count_full_amplitude(0x0F) == 4
    ok2, _ = dev.safety_check(0x0F, override=True)
    assert ok2


def test_play_multi_returns_safety_limit_error():
    dev = make_device()
    for ch in range(8):
        prof = dev.get_profile(ch)
        prof.mode = C.MODE_CONT
        prof.drv1_lvl = 127
        dev.set_profile(ch, prof)
    with pytest.raises(DeviceError) as ei:
        dev.play_multi(0xFF)
    assert ei.value.errcode == C.ERR_SAFETY_LIMIT


def test_play_multi_within_limit_succeeds():
    dev = make_device()
    resp = dev.play_multi(0x03)
    assert resp.started_mask == 0x03


def test_trig_pulse_expands_shared_channels():
    dev = make_device()
    for ch in range(C.CHANNEL_COUNT):
        dev.profiles[ch] = dev.get_profile(ch)
    started = dev.trig_pulse(0x20, width_us=20, count=1)   # 只请求 CH5
    assert started == 0xE0, "CH5/6/7 共用 GPIO14，硬触发下三者必然一起动"
    assert C.expand_shared_trig_mask(0x20) == 0xE0


# --------------------------------------------------------------------------
# 寄存器
# --------------------------------------------------------------------------


def test_reg_read_write_update():
    dev = make_device()
    dev.reg_write(0, 0x1C, 0x85)
    assert dev.reg_read_u8(0, 0x1C) == 0x85
    r = dev.reg_update(0, 0x1C, 0x7F, 0x40)
    assert r.old == 0x85 and r.new == (0x85 & 0x80) | 0x40


def test_reg_dump_full_covers_range():
    dev = make_device()
    dump = dev.reg_dump_full(0)
    assert 0x00 in dump and 0x69 in dump
    assert len(dump) == 64 + 42


def test_reg_read_multi_concatenates():
    dev = make_device()
    dev.reg_write(0, 0x10, b"\x01\x02\x03")
    data = dev.reg_read_multi(0, [(0x10, 2), (0x12, 1)])
    assert data == b"\x01\x02\x03"


# --------------------------------------------------------------------------
# 波形
# --------------------------------------------------------------------------


def test_upload_waveform_and_readback_verify():
    dev = make_device()
    samples = bytes(((i * 7) % 256) for i in range(600))
    progress = []
    resp = dev.upload_waveform(0, [(1, samples)], C.RATE_24K,
                               progress=lambda d, t: progress.append((d, t)))
    assert resp.result == 0
    assert progress[-1] == (600, 600)
    # WAVE_ADD 每片 ≤184 样本 → 600/184 = 4 片
    assert len(progress) == 4
    assert dev.verify_waveform(0, [(1, samples)]) == []


def test_upload_waveform_rejects_overflow():
    dev = make_device()
    huge = bytes(3000)
    with pytest.raises(DeviceError) as ei:
        dev.upload_waveform(0, [(1, huge)], C.RATE_24K)
    assert ei.value.errcode in (C.ERR_SRAM_OVERFLOW, C.ERR_BAD_PARAM)


def test_wave_begin_rejects_48k():
    dev = make_device()
    with pytest.raises(DeviceError) as ei:
        dev.wave_begin(0, C.RATE_48K, 1)
    assert ei.value.errcode == C.ERR_UNSUPPORTED


def test_verify_waveform_reports_mismatch():
    dev = make_device()
    upload = bytes(range(64))
    dev.upload_waveform(0, [(1, upload)], C.RATE_24K)
    other = bytes(range(1, 65))
    bad = dev.verify_waveform(0, [(1, other)])
    assert bad and bad[0][0] == 1


# --------------------------------------------------------------------------
# 测量
# --------------------------------------------------------------------------


def test_f0_measure_returns_valid_result():
    dev = make_device()
    r = dev.f0_measure(0, timeout_ms=300)
    assert r.valid == 1
    assert r.raw == 0x0B70
    assert abs(r.f0_hz - 192000.0 / 0x0B70) < 1e-6
    assert r.diagnosis() == "" or "可疑" in r.diagnosis()


def test_f0_result_diagnosis_when_invalid():
    r = K.F0Result(valid=0, raw_h=0, raw_l=0, f0_hz_x10=0xFFFF)
    text = r.diagnosis()
    assert "C-1" in text or "电容" in text
    assert r.f0_hz is None          # raw=0 → 除零保护


def test_det_read_vbat_and_rl():
    dev = make_device()
    v = dev.det_read(0, C.DET_SEQ_VBAT)
    assert v.valid == 1
    assert abs(v.value() - K.DetReadResp.vbat_v(v.avg_data)) < 0.02
    rl = dev.det_read(0, C.DET_SEQ_RL)
    assert abs(rl.value(8) - K.DetReadResp.rl_ohm(rl.avg_data, 8)) < 0.02


def test_snapshot_decodes_registers():
    dev = make_device()
    s = dev.snapshot(0)
    assert s.present == 1
    assert s.chipid_h == 0x23 and s.chipid_l == 0x40
    assert abs(s.f0_hz - 192000.0 / 0x0B70) < 1e-6
    assert s.glb_state_name == "STANDBY"
    assert abs(s.vbat_v() - 6.1 * s.avg_data / 1023.0) < 1e-6


def test_play_state_and_stop():
    dev = make_device()
    dev.set_mode(0, C.MODE_RAM)
    dev.play(0, timeout_ms=1000)
    assert dev.get_play_state(0).playing == 1
    dev.stop(0)
    assert dev.get_play_state(0).playing == 0


def test_stop_all_reports_mask():
    dev = make_device()
    resp = dev.play_multi(0x0F)
    assert resp.started_mask == 0x0F
    mask = dev.stop_all()
    assert mask == 0x0F


# --------------------------------------------------------------------------
# 测试组
# --------------------------------------------------------------------------


def test_sweep_and_abort():
    dev = make_device()
    r = dev.sweep_amp(0, 0, 16, 127, 16, dwell_ms=10, settle_ms=0)
    assert r.accepted == 1 and r.total_steps == 7   # 16..127 步长 16 → 7 档
    dev.sweep_abort()
    r2 = dev.sweep_freq(0, 100, 200, 50, dwell_ms=10)
    assert r2.accepted == 1 and r2.total_steps == 3


def test_sweep_step_zero_rejected():
    dev = make_device()
    with pytest.raises(DeviceError) as ei:
        dev.sweep_amp(0, 0, 0, 127, 0)
    assert ei.value.errcode == C.ERR_BAD_PARAM


def test_selftest_and_test_run_accepted():
    dev = make_device()
    assert dev.selftest() is True
    assert dev.test_run(1) is True


# --------------------------------------------------------------------------
# 总线
# --------------------------------------------------------------------------


def test_bus_probe_and_speed_and_pullup():
    dev = make_device()
    b = dev.bus_probe()
    assert b.sel_used == 1 and b.sda_gpio == 6 and b.scl_gpio == 7
    assert dev.bus_set_speed(100000) == 100000
    with pytest.raises(ValueError):
        dev.bus_set_speed(250000)
    dev.bus_set_pullup(True)
    assert dev.get_caps().caps & C.CAP_INTERNAL_PULLUP


def test_bus_probe_without_tca_reports_error():
    dev = AWTestDevice(FakeDeviceTransport(with_tca=False))
    dev.t.open("FAKE")
    dev.t.enter_binary_mode()
    with pytest.raises(DeviceError) as ei:
        dev.bus_probe()
    assert ei.value.errcode == C.ERR_TCA_NOT_FOUND


def test_tca_mask_rejects_multiple_bits():
    dev = make_device()
    with pytest.raises(DeviceError) as ei:
        dev.tca_set_mask(0x03)
    assert ei.value.errcode == C.ERR_BAD_PARAM
    assert dev.tca_set_mask(0x00) == 0
    assert dev.tca_set_mask(0x08) == 0x08


# --------------------------------------------------------------------------
# 安全参数
# --------------------------------------------------------------------------


def test_safety_get_set_and_persist():
    dev = make_device()
    cfg = dev.get_safety()
    assert cfg.play_timeout_ms == C.DEFAULT_PLAY_TIMEOUT_MS
    assert cfg.max_simul_full_amp == C.DEFAULT_MAX_SIMUL_FULL_AMP
    cfg.play_timeout_ms = 800
    cfg.max_simul_full_amp = 4
    back = dev.set_safety(cfg)
    assert back.play_timeout_ms == 800 and back.max_simul_full_amp == 4


def test_safety_set_rejects_out_of_range():
    dev = make_device()
    cfg = K.SafetyCfg(play_timeout_ms=10, max_simul_full_amp=99,
                      heartbeat_timeout_ms=100)
    with pytest.raises(DeviceError) as ei:
        dev.set_safety(cfg)
    assert ei.value.errcode == C.ERR_BAD_PARAM
