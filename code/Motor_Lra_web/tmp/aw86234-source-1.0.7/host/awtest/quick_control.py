"""Quick deployment orchestration. No GUI or serial framing dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable

from awtest import profiles as P
from awtest.protocol import commands as C


def continuous_profile(level: int = 40, frequency: float = 168.0) -> P.Profile:
    """Measured sustained CONT recipe; session watchdogs remain enabled."""
    p = P.Profile()
    p.mode = C.MODE_CONT
    p.set_f0_hz(frequency)
    p.drv1_lvl = level
    p.drv2_lvl = 0
    p.drv1_time, p.drv2_time = 255, 0
    p.brk_time, p.wait_num = 0, 0
    p.track_en, p.en_f0_det = 1, 0
    p.sin_mode, p.edge_fre = 1, 14
    return p


class DeploymentCancelled(RuntimeError):
    pass


# Firmware aw_apply_profile writes common fields plus ONLY the selected segment.
# GET_PROFILE reads all segments, including registers not written by this request.
COMMON_FIELDS = frozenset("profile_ver mode gain brk_en stop_mode vbat_mode vbat_ref wavdat_mode".split())
MODE_FIELDS = {
    C.MODE_CONT: frozenset("en_f0_det sin_mode edge_fre f_pre drv_width wait_num brk_gain track_en drv1_lvl drv2_lvl drv1_time drv2_time brk_time track_margin".split()),
    C.MODE_RAM: frozenset("wavseq mainloop waitslot seq_loop".split()),
    C.MODE_TRIG: frozenset("en_fir trig1_pos_en trig1_seq_p trig1_neg_en trig1_seq_n trig1_polar trig1_lev trig1_brk trig1_stop trig1_prio trg_gain trig1_mode intn_pin start_dly".split()),
    C.MODE_RTP: frozenset("rtp_flags base_addr fifo_ae fifo_af".split()),
}


def applied_diffs(profile, diffs):
    fields = COMMON_FIELDS | MODE_FIELDS[profile.mode]
    if profile.mode != C.MODE_CONT:
        fields |= {'en_f0_det', 'brk_time'}
    if profile.mode in (C.MODE_RAM, C.MODE_TRIG):
        fields |= {'wait_num'}
    return [difference for difference in diffs if difference[0] in fields]


@dataclass(frozen=True)
class DeploymentResult:
    mask: int
    mode: int
    started_mask: int = 0


def preflight(device, profile, mask, timeout_ms, start, cancelled, progress=lambda _: None, *, check_waves=True):
    """Read-only validation before any preset replacement or configuration write."""
    def check():
        if cancelled.is_set():
            raise DeploymentCancelled("部署已取消，未执行后续启动。")

    def call(fn, *args, **kwargs):
        check()
        value = fn(*args, **kwargs)
        check()
        return value

    if not 0 < mask <= 0xFF:
        raise ValueError("请选择至少一个通道。")
    if not 100 <= timeout_ms <= 60000:
        raise ValueError("播放时长必须在 100..60000 ms 内。")
    problems = profile.validate()
    if problems:
        raise ValueError("；".join(f"{key}: {why}" for key, why in problems))
    if start and profile.mode == C.MODE_RTP:
        raise ValueError("RTP 需要数据流；请在高级调试中配置并运行流式脚本。")
    if profile.mode == C.MODE_TRIG and (profile.trig1_lev or profile.trig1_polar or
            not profile.trig1_pos_en or profile.trig1_neg_en):
        raise ValueError("快捷触发仅支持上升沿单次触发；其他触发方式请用高级调试。")
    if profile.mode in (C.MODE_RAM, C.MODE_TRIG) and profile.wait_num == 0:
        raise ValueError("RAM/TRIG 的 WAIT_NUM 不能为 0，否则可能无法结束；请恢复对应模式默认参数。")
    channels = [ch for ch in range(8) if mask & (1 << ch)]
    progress("检查在线通道…")
    caps = call(device.get_caps)
    if profile.mode == C.MODE_TRIG and caps.has(C.CAP_SHARED_TRIG_567):
        expanded = C.expand_shared_trig_mask(mask)
        if expanded != mask:
            raise ValueError("此设备的 CH5/6/7 共用触发线，请明确勾选这三路后再下发。")
    if start and profile.mode in (C.MODE_CONT, C.MODE_RAM) and not caps.has(C.CAP_TIMED_PLAY):
        raise ValueError("请先更新至支持定时播放的固件（1.0.1）；旧固件会把到时停止报告为安全超时。")
    if profile.mode == C.MODE_TRIG and not caps.has(C.CAP_BOOT_TRIGGER):
        raise ValueError("请先更新至支持 BOOT 波形触发的固件（1.0.1）。")
    probe = call(device.ch_probe_all)
    if mask & ~probe.present_mask:
        missing = mask & ~probe.present_mask
        raise ValueError("所选通道离线：" + "、".join(
            f"CH{ch}" for ch in channels if missing & (1 << ch)))
    if check_waves and ((start and profile.mode == C.MODE_RAM) or profile.mode == C.MODE_TRIG):
        ids = ({v for v in profile.wavseq if 0 < v < 128}
               if profile.mode == C.MODE_RAM else
               {v for v, enabled in ((profile.trig1_seq_p, profile.trig1_pos_en),
                                      (profile.trig1_seq_n, profile.trig1_neg_en)) if enabled})
        if not ids:
            raise ValueError("尚未选择播放波形，请先在波形编辑页部署波形。")
        for ch in channels:
            library = call(device.wave_get_library, ch)
            missing = ids - {entry.wave_id for entry in library.entries}
            if missing:
                raise ValueError(f"CH{ch} 缺少波形 {sorted(missing)}，请先在波形编辑页部署。")

    return caps, channels


def deploy(device, profile: P.Profile, mask: int, timeout_ms: int,
           start: bool, cancelled: Event,
           progress: Callable[[str], None] = lambda _: None) -> DeploymentResult:
    """Stop, configure and verify every target before allowing any start.

    The caller owns one immutable snapshot per request and a cancellation event.
    Emergency stop must set that event and queue STOP after the running task;
    this prevents an in-flight PLAY from overtaking the final STOP.
    """
    def check():
        if cancelled.is_set():
            raise DeploymentCancelled("部署已取消，未执行后续启动。")

    def call(fn, *args, **kwargs):
        check()
        value = fn(*args, **kwargs)
        check()
        return value

    caps, channels = preflight(device, profile, mask, timeout_ms, start, cancelled, progress)

    touched = False
    try:
        if caps.has(C.CAP_BOOT_TRIGGER):
            call(device.boot_arm, 0)
        progress("停止目标通道，准备切换模式…")
        for ch in channels:
            touched = True
            call(device.stop, ch, immediate=True)
        for i, ch in enumerate(channels):
            progress(f"下发并回读校验 {i + 1}/{len(channels)} · CH{ch}")
            if profile.mode != C.MODE_CONT:
                # These CONT registers also gate the common braking engine.
                # SET_PROFILE for RAM/TRIG/RTP does not restore them by itself.
                call(device.reg_update, ch, 0x17, 0x20, profile.en_f0_det << 5)
                call(device.reg_write, ch, 0x20, bytes([profile.brk_time]))
            if profile.mode in (C.MODE_RAM, C.MODE_TRIG):
                call(device.reg_write, ch, 0x1A, bytes([profile.wait_num]))
            diffs = call(device.set_profile_verify, ch, profile)
            diffs = applied_diffs(profile, diffs)
            if diffs:
                detail = "；".join(f"{name}: 期望 {expected}，读回 {actual}"
                                  for name, expected, actual in diffs)
                raise ValueError(f"CH{ch} 校验不一致，已阻止启动：{detail}")
        if profile.mode == C.MODE_TRIG:
            call(device.boot_arm, mask)
        started = 0
        if start:
            progress("全部参数校验通过，准备启动…")
            allowed, reason = call(device.safety_check, mask, override=False)
            if not allowed:
                raise ValueError(reason)
            if profile.mode == C.MODE_TRIG:
                started = call(device.trig_pulse, mask, width_us=20, count=1,
                               period_ms=100, initial_delay_us=0)
            else:
                response = call(device.play_timed, mask, duration_ms=timeout_ms)
                started = response.started_mask
            if started != mask:
                raise RuntimeError(f"启动应答不完整：请求 0x{mask:02X}，实际 0x{started:02X}。")
            # TRIG_PULSE does not emit EVT_PLAY_STARTED in this firmware.
            # Query actual state instead of inferring "still playing" from its ACK.
            if profile.mode == C.MODE_TRIG:
                for ch in channels:
                    call(device.get_play_state, ch)
        return DeploymentResult(mask, profile.mode, started)
    except Exception:
        # No rollback of profiles: a partial write must remain visible as failure.
        # Always stop affected channels, including a PLAY that returned after cancel.
        if touched:
            if caps.has(C.CAP_BOOT_TRIGGER):
                try:
                    device.boot_arm(0)
                except Exception:
                    pass
            for ch in channels:
                try:
                    device.stop(ch, immediate=True)
                except Exception:
                    pass  # The original failure is retained; firmware watchdog remains active.
            if not cancelled.is_set():
                for ch in channels:
                    try:
                        info = device.ch_get_info(ch)
                        device.modes[ch] = info.mode
                        device.playing[ch] = bool(info.playing)
                    except Exception:
                        pass  # Original failure remains visible; do not claim confirmed state.
        raise
