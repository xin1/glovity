# -*- coding: utf-8 -*-
"""面向对象的高层设备 API。

界面、CLI、脚本引擎都只通过这一层访问设备，**不直接拼字节**。
每一 个方法对应协议里的一条命令，返回已解码的 dataclass；错误应答抛 ``DeviceError``。

不依赖 GUI，可单独 pytest（配合 ``FakeTransport`` 做收发回环测试）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import profiles as P
from .live_lab import LiveParams
from .protocol import codec as K
from .protocol import commands as C
from .protocol.framing import CHAN_ALL, CHAN_NONE, Frame
from .protocol.transport import (DeviceError, PortInfo, RequestTimeout,
                                 SerialTransport, Stats, TransportError,
                                 list_ports)

# 各类命令的建议超时（协议 §2）
TIMEOUT_FAST = 0.3
TIMEOUT_NORMAL = 0.5
TIMEOUT_SLOW = 2.0
TIMEOUT_WAVE = 2.0


@dataclass
class ConnectionInfo:
    """连接后一次性汇总的信息（界面状态栏直接用）。"""

    port: str = ""
    ping: Optional[K.PingResp] = None
    caps: Optional[K.Caps] = None
    status: Optional[K.Status] = None
    bus: Optional[K.BusProbeResp] = None
    channels: Optional[K.ChProbeAllResp] = None

    @property
    def present_mask(self) -> int:
        return self.channels.present_mask if self.channels else 0

    def summary(self) -> str:
        parts = [f"已连接 {self.port}"]
        if self.ping:
            parts.append(f"fw {self.ping.fw_version}")
        if self.caps:
            parts.append(f"{self.caps.channel_count} 通道")
        if self.bus:
            parts.append(f"{self.bus.bus_name}(IO{self.bus.sda_gpio}/IO{self.bus.scl_gpio})"
                         f"@{self.bus.speed_hz // 1000}kHz")
        if self.channels:
            n = bin(self.channels.present_mask).count("1")
            parts.append(f"在线 {n} 路")
        return " · ".join(parts)


class AWTestDevice:
    """AW86234 八路振动测试板的高层 API。"""

    def __init__(self, transport: Optional[SerialTransport] = None) -> None:
        self.t = transport or SerialTransport()
        self.info = ConnectionInfo()
        self._lock = threading.RLock()
        #: 通道配置缓存（Profile），软复位后用于重放
        self.profiles: Dict[int, P.Profile] = {}
        self.modes: Dict[int, int] = {ch: C.MODE_RAM for ch in range(C.CHANNEL_COUNT)}
        self.playing: Dict[int, bool] = {ch: False for ch in range(C.CHANNEL_COUNT)}
        #: RL 检测时固件实际使用的 D2S_GAIN 倍率（= DET_RL_D2S_GAIN = 20）。
        #: ★ 不是寄存器默认的 ×8 —— 固件按手册 §LRA 阻抗检测 第 3 步显式写入 ×20。
        #: 权威换算值来自固件回报的 value_x100；这个属性只供本地重算/展示使用。
        self.d2s_gain: int = C.DET_RL_D2S_GAIN

    # ==================================================================
    # 连接
    # ==================================================================

    @staticmethod
    def list_ports(only_espressif: bool = False) -> List[PortInfo]:
        return list_ports(only_espressif)

    def connect(self, port: str, baudrate: int = 115200,
                bus_sel: int = K.BusProbeResp.BUS_AUTO,
                probe_channels: bool = True,
                heartbeat: bool = True,
                on_step: Optional[Callable[[str], None]] = None) -> ConnectionInfo:
        """按 `05` 文档 §3.3 的连接流程：MODE BIN → PING → CAPS → STATUS → BUS_PROBE → CH_PROBE_ALL。"""

        def step(msg: str) -> None:
            if on_step:
                on_step(msg)

        step(f"打开串口 {port}")
        self.t.open(port, baudrate)
        try:
            step("切换到二进制协议模式")
            if not self.t.enter_binary_mode():
                raise TransportError("固件未响应 MODE BIN（可能不在 ASCII 模式，或串口被占用）")

            step("PING")
            info = ConnectionInfo(port=port)
            info.ping = self.ping()
            step("GET_CAPS")
            info.caps = self.get_caps()
            step("GET_STATUS")
            info.status = self.get_status()
            step("BUS_PROBE")
            try:
                info.bus = self.bus_probe(bus_sel)
            except DeviceError as exc:
                step(f"总线探测失败：{exc}")
                info.bus = None
            if probe_channels:
                step("CH_PROBE_ALL")
                try:
                    info.channels = self.ch_probe_all()
                    for chan, hid, lid, _st in info.channels.entries:
                        if (hid << 8) | lid == C.AW_CHIPID_EXPECTED:
                            self.profiles.setdefault(chan, P.Profile())
                except DeviceError as exc:
                    step(f"通道探测失败：{exc}")
                    info.channels = None
            self.info = info
            if heartbeat:
                try:
                    hb_ms = self.get_safety().heartbeat_timeout_ms
                except Exception:
                    hb_ms = C.DEFAULT_HEARTBEAT_TIMEOUT_MS
                self.t.start_heartbeat(max(0.2, hb_ms / 3000.0))
            step("连接完成")
            return info
        except Exception:
            self.t.close()
            raise

    def disconnect(self) -> None:
        """关闭串口（尽力先发 STOP_ALL + TRIG_PARK）。"""
        try:
            if self.t.is_open and self.t.binary_mode:
                try:
                    self.t.stop_all(1)
                    self.t.trig_park()
                except Exception:
                    pass
        finally:
            self.t.close()

    @property
    def is_open(self) -> bool:
        return self.t.is_open

    @property
    def stats(self) -> Stats:
        return self.t.stats

    # ==================================================================
    # 系统组
    # ==================================================================

    def ping(self) -> K.PingResp:
        return K.PingResp.decode(self.t.request(C.CMD_PING, b"", timeout=TIMEOUT_FAST).payload)

    def get_caps(self) -> K.Caps:
        return K.Caps.decode(self.t.request(C.CMD_GET_CAPS, b"", timeout=TIMEOUT_NORMAL).payload)

    def get_status(self) -> K.Status:
        return K.Status.decode(self.t.request(C.CMD_GET_STATUS, b"", timeout=TIMEOUT_NORMAL).payload)

    def get_safety(self) -> K.SafetyCfg:
        return K.SafetyCfg.decode(
            self.t.request(C.CMD_GET_SAFETY, b"", timeout=TIMEOUT_NORMAL).payload)

    def set_safety(self, cfg: K.SafetyCfg) -> K.SafetyCfg:
        self.t.request(C.CMD_SET_SAFETY, cfg.encode(), timeout=TIMEOUT_NORMAL)
        return self.get_safety()

    def clear_errors(self) -> None:
        self.t.request(C.CMD_CLEAR_ERRORS, b"", timeout=TIMEOUT_NORMAL)

    def mcu_reset(self, delay_ms: int = 100) -> None:
        self.t.request(C.CMD_MCU_RESET, K.u16(delay_ms), timeout=TIMEOUT_NORMAL)

    def set_log_level(self, level: int) -> None:
        self.t.request(C.CMD_SET_LOG_LEVEL, K.u8(level), timeout=TIMEOUT_NORMAL)

    def set_proto_mode(self, mode: int) -> None:
        self.t.request(C.CMD_SET_PROTO_MODE, K.u8(mode), timeout=TIMEOUT_NORMAL)

    # ==================================================================
    # 总线组
    # ==================================================================

    def bus_probe(self, bus_sel: int = K.BusProbeResp.BUS_AUTO) -> K.BusProbeResp:
        resp = self.t.request(C.CMD_BUS_PROBE, K.u8(bus_sel), timeout=1.0)
        return K.BusProbeResp.decode(resp.payload)

    def bus_set_speed(self, hz: int) -> int:
        if hz not in (C.I2C_SPEED_SAFE, C.I2C_SPEED_FAST):
            raise ValueError("只接受 100000 或 400000")
        resp = self.t.request(C.CMD_BUS_SET_SPEED, K.u32(hz), timeout=TIMEOUT_NORMAL)
        return int.from_bytes(resp.payload[:4], "little")

    def bus_set_pullup(self, enable: bool) -> None:
        self.t.request(C.CMD_BUS_SET_PULLUP, K.u8(1 if enable else 0), timeout=TIMEOUT_NORMAL)

    def bus_recover(self) -> int:
        resp = self.t.request(C.CMD_BUS_RECOVER, b"", timeout=TIMEOUT_SLOW)
        return resp.payload[0] if resp.payload else 0

    def tca_set_mask(self, mask: int) -> int:
        resp = self.t.request(C.CMD_TCA_SET_MASK, K.u8(mask), timeout=TIMEOUT_NORMAL)
        return resp.payload[0] if resp.payload else 0

    def tca_get_mask(self) -> int:
        resp = self.t.request(C.CMD_TCA_GET_MASK, b"", timeout=TIMEOUT_NORMAL)
        return resp.payload[0] if resp.payload else 0

    def tca_reset(self, hold_us: int = 10) -> None:
        self.t.request(C.CMD_TCA_RESET, K.u16(hold_us), timeout=TIMEOUT_NORMAL)

    def tca_read_ctrl(self) -> int:
        resp = self.t.request(C.CMD_TCA_READ_CTRL, b"", timeout=TIMEOUT_NORMAL)
        return resp.payload[0] if resp.payload else 0

    # ==================================================================
    # 通道组
    # ==================================================================

    def ch_probe(self, chan: int) -> K.ChProbeResp:
        resp = self.t.request(C.CMD_CH_PROBE, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        return K.ChProbeResp.decode(resp.payload)

    def ch_probe_all(self) -> K.ChProbeAllResp:
        resp = self.t.request(C.CMD_CH_PROBE_ALL, b"", timeout=TIMEOUT_SLOW)
        return K.ChProbeAllResp.decode(resp.payload)

    def ch_get_info(self, chan: int) -> K.ChInfo:
        resp = self.t.request(C.CMD_CH_GET_INFO, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        info = K.ChInfo.decode(resp.payload)
        self.modes[chan] = info.mode
        self.playing[chan] = bool(info.playing)
        return info

    # ==================================================================
    # 寄存器组
    # ==================================================================

    def reg_read(self, chan: int, addr: int, length: int = 1) -> bytes:
        resp = self.t.request(C.CMD_REG_READ, K.enc_reg_read(addr, length),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return resp.payload

    def reg_read_u8(self, chan: int, addr: int) -> int:
        data = self.reg_read(chan, addr, 1)
        return data[0] if data else 0

    def reg_write(self, chan: int, addr: int, data) -> None:
        if isinstance(data, int):
            data = bytes([data & 0xFF])
        self.t.request(C.CMD_REG_WRITE, K.enc_reg_write(addr, bytes(data)),
                       chan=chan, timeout=TIMEOUT_NORMAL)

    def reg_update(self, chan: int, addr: int, mask: int, value: int) -> K.RegUpdateResp:
        resp = self.t.request(C.CMD_REG_UPDATE, K.enc_reg_update(addr, mask, value),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return K.RegUpdateResp.decode(resp.payload)

    def reg_read_multi(self, chan: int, items: Sequence[Tuple[int, int]]) -> bytes:
        resp = self.t.request(C.CMD_REG_READ_MULTI, K.enc_reg_read_multi(items),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return resp.payload

    def reg_dump(self, chan: int, start: int, length: int) -> K.RegDumpResp:
        resp = self.t.request(C.CMD_REG_DUMP, K.enc_reg_dump(start, length),
                              chan=chan, timeout=TIMEOUT_SLOW)
        return K.RegDumpResp.decode(resp.payload)

    def reg_dump_full(self, chan: int) -> Dict[int, int]:
        """整片 dump 0x00~0x69（协议建议分两次请求）。"""
        out: Dict[int, int] = {}
        for start, ln in ((0x00, 64), (0x40, 42)):
            r = self.reg_dump(chan, start, ln)
            for i, b in enumerate(r.data):
                out[start + i] = b
        return out

    def stream_write(self, chan: int, addr: int, offset: int, data: bytes) -> None:
        self.t.request(C.CMD_STREAM_WRITE, K.enc_stream_write(addr, offset, data),
                       chan=chan, timeout=TIMEOUT_SLOW)

    # ==================================================================
    # 器件组
    # ==================================================================

    def aw_softrst(self, chan: int, replay_profile: bool = True) -> None:
        """软复位；本板唯一的软件复位手段（无硬件复位、无电源开关）。"""
        self.t.request(C.CMD_AW_SOFTRST, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        self.playing[chan] = False
        if replay_profile and chan in self.profiles:
            # 固件侧已经重放，这里读取回读值刷新缓存
            try:
                self.get_profile(chan)
            except Exception:
                pass

    def aw_wake(self, chan: int) -> None:
        self.t.request(C.CMD_AW_WAKE, b"", chan=chan, timeout=TIMEOUT_NORMAL)

    def aw_standby(self, chan: int) -> None:
        self.t.request(C.CMD_AW_STANDBY, b"", chan=chan, timeout=TIMEOUT_NORMAL)

    def aw_init_default(self, chan: int) -> K.InitDefaultResp:
        resp = self.t.request(C.CMD_AW_INIT_DEFAULT, b"", chan=chan, timeout=TIMEOUT_SLOW)
        return K.InitDefaultResp.decode(resp.payload)

    def aw_get_state(self, chan: int) -> K.AwState:
        resp = self.t.request(C.CMD_AW_GET_STATE, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        return K.AwState.decode(resp.payload)

    # ==================================================================
    # 播放组
    # ==================================================================

    def set_mode(self, chan: int, mode: int) -> None:
        if mode == C.MODE_TRIG:
            self._ensure_trig_safe(chan)
        self.t.request(C.CMD_SET_MODE, K.u8(mode), chan=chan, timeout=TIMEOUT_NORMAL)
        self.modes[chan] = mode

    def _ensure_trig_safe(self, chan: int) -> None:
        """协议 §11.1：进入 TRIG 前必须确认 0x3A bit2 TRG1_STOP = 1。

        界面/脚本不允许关闭它；这里做客户端侧的兜底检查，固件侧还会再校验一次。
        """
        reg3a = self.reg_read_u8(chan, 0x3A)
        if not (reg3a & 0x04):
            raise DeviceError(C.CMD_SET_MODE, chan, C.ERR_VERIFY_FAILED,
                              "0x3A bit2 TRG1_STOP = 0：TRIG 播放无法立即停止，已拒绝进入 TRIG 模式")

    def play(self, chan: int, timeout_ms: int = 0, override: bool = False) -> K.PlayResp:
        resp = self.t.request(C.CMD_PLAY, K.enc_play(timeout_ms, 1 if override else 0),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        self.playing[chan] = True
        return K.PlayResp.decode(resp.payload)

    def stop(self, chan: int, immediate: bool = True) -> None:
        self.t.request(C.CMD_STOP, K.u8(1 if immediate else 0), chan=chan,
                       timeout=TIMEOUT_NORMAL)
        self.playing[chan] = False

    def play_multi(self, chan_mask: int, timeout_ms: int = 0, skew_us: int = 0,
                   override: bool = False) -> K.PlayMultiResp:
        resp = self.t.request(C.CMD_PLAY_MULTI,
                              K.enc_play_multi(chan_mask, timeout_ms, skew_us,
                                               1 if override else 0),
                              timeout=TIMEOUT_NORMAL)
        out = K.PlayMultiResp.decode(resp.payload)
        for ch in range(C.CHANNEL_COUNT):
            if out.started_mask & (1 << ch):
                self.playing[ch] = True
        return out

    def play_timed(self, chan_mask: int, duration_ms: int) -> K.PlayMultiResp:
        if not 0 < chan_mask <= 255 or not 100 <= duration_ms <= 60000:
            raise ValueError("定时播放需有效通道，时长 100..60000 ms")
        resp = self.t.request(C.CMD_PLAY_TIMED, K.u8(chan_mask) + K.u16(duration_ms),
                              timeout=TIMEOUT_NORMAL)
        if len(resp.payload) != 1:
            raise ValueError("定时播放应答长度错误")
        out = K.PlayMultiResp(resp.payload[0], 0)
        for ch in range(C.CHANNEL_COUNT):
            if out.started_mask & (1 << ch):
                self.playing[ch] = True
        return out

    def boot_arm(self, chan_mask: int) -> None:
        resp = self.t.request(C.CMD_BOOT_ARM, K.u8(chan_mask), timeout=TIMEOUT_NORMAL)
        if resp.payload != K.u8(chan_mask):
            raise ValueError("BOOT 启用应答与请求通道不一致")

    def live_control(self, mask: int, params: LiveParams, start: bool = False) -> None:
        """Start or update firmware synthesis; require an exact acknowledgement."""
        if type(mask) is not int or not 1 <= mask <= 255:
            raise ValueError("实时播放通道掩码必须为 1..255 的整数")
        if type(start) is not bool:
            raise ValueError("start 必须为布尔值")
        if not isinstance(params, LiveParams):
            raise ValueError("params 必须为 LiveParams")
        payload = bytes((0 if start else 1, mask)) + params.encode_fields()
        resp = self.t.request(C.CMD_LIVE_CONTROL, payload, timeout=TIMEOUT_NORMAL)
        if resp.payload != payload:
            raise ValueError("实时播放应答与请求不一致")
        for ch in range(C.CHANNEL_COUNT):
            if mask & (1 << ch):
                self.modes[ch] = C.MODE_CONT if params.tracking else C.MODE_RTP
                if start:
                    self.playing[ch] = True

    def tactile_frame(self, mask: int, sequence: int, levels, start: bool = False) -> None:
        """One atomic eight-channel vector; only fresh sequences renew the 300ms lease."""
        import struct
        if type(mask) is not int or not 1 <= mask <= 255 or type(start) is not bool:
            raise ValueError("invalid tactile mask/action")
        if type(sequence) is not int or not 0 <= sequence <= 65535:
            raise ValueError("invalid tactile sequence")
        if len(levels) != 8 or any(type(x) is not int or not 0 <= x <= 60 for x in levels):
            raise ValueError("tactile requires eight integer levels 0..60")
        if any(x and (start or not mask & (1 << ch)) for ch, x in enumerate(levels)):
            raise ValueError("START and unselected channels must be silent")
        if sum(x*x for x in levels) > 7200:
            raise ValueError("tactile vector exceeds output budget")
        payload = struct.pack("<BBH8B", int(not start), mask, sequence, *levels)
        response = self.t.request(C.CMD_TACTILE_FRAME, payload, timeout=.15 if not start else .5)
        if response.payload != payload:
            raise ValueError("tactile acknowledgement mismatch")
        for ch in range(8):
            if mask & (1 << ch):
                self.modes[ch] = C.MODE_CONT
                self.playing[ch] = True

    def live_keepalive(self, mask: int) -> None:
        """Renew only the selected live session's independent firmware lease."""
        if type(mask) is not int or not 1 <= mask <= 255:
            raise ValueError("实时播放通道掩码必须为 1..255 的整数")
        payload = bytes((mask,))
        resp = self.t.request(C.CMD_LIVE_KEEPALIVE, payload, timeout=TIMEOUT_NORMAL)
        if resp.payload != payload:
            raise ValueError("实时播放续租应答与请求不一致")

    def trig_pulse(self, chan_mask: int, width_us: int = 20, count: int = 1,
                   period_ms: int = 100, initial_delay_us: int = 0) -> int:
        resp = self.t.request(C.CMD_TRIG_PULSE,
                              K.enc_trig_pulse(chan_mask, width_us, count, period_ms,
                                               initial_delay_us),
                              timeout=TIMEOUT_SLOW)
        return resp.payload[0] if resp.payload else 0

    def trig_level(self, chan_mask: int, level: int, hold_ms: int) -> None:
        self.t.request(C.CMD_TRIG_LEVEL, K.enc_trig_level(chan_mask, level, hold_ms),
                       timeout=TIMEOUT_SLOW)

    def trig_pattern(self, chan_mask: int, on_us: int = 20, off_us: int = 20,
                     count: int = 10, period_ms: int = 100) -> None:
        self.t.request(C.CMD_TRIG_PATTERN,
                       K.enc_trig_pattern(chan_mask, on_us, off_us, count, period_ms),
                       timeout=TIMEOUT_SLOW)

    def get_play_state(self, chan: int) -> K.PlayState:
        resp = self.t.request(C.CMD_GET_PLAY_STATE, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        st = K.PlayState.decode(resp.payload)
        self.playing[chan] = bool(st.playing)
        return st

    def stop_all(self, immediate: bool = True) -> int:
        resp = self.t.request(C.CMD_STOP_ALL, K.u8(1 if immediate else 0),
                              timeout=TIMEOUT_NORMAL)
        self.playing = {ch: False for ch in range(C.CHANNEL_COUNT)}
        out = K.StopAllResp.decode(resp.payload)
        return out.stopped_mask

    def trig_park(self) -> None:
        self.t.request(C.CMD_TRIG_PARK, b"", timeout=TIMEOUT_NORMAL)

    # ==================================================================
    # 参数组
    # ==================================================================

    def set_profile(self, chan: int, profile: P.Profile) -> K.SetProfileResp:
        problems = profile.validate()
        if problems:
            name, why = problems[0]
            raise DeviceError(C.CMD_SET_PROFILE, chan, C.ERR_BAD_PARAM, f"{name}：{why}")
        resp = self.t.request(C.CMD_SET_PROFILE, profile.to_bytes(), chan=chan,
                              timeout=TIMEOUT_SLOW)
        out = K.SetProfileResp.decode(resp.payload)
        self.profiles[chan] = profile.copy()
        self.modes[chan] = profile.mode
        return out

    def get_profile(self, chan: int) -> P.Profile:
        resp = self.t.request(C.CMD_GET_PROFILE, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        prof = P.Profile.from_bytes(resp.payload)
        self.profiles[chan] = prof
        return prof

    def set_profile_verify(self, chan: int, profile: P.Profile
                           ) -> List[Tuple[str, object, object]]:
        """**下发 → 回读 → 比对**（验收标准第 4 条）。

        返回不一致项列表；空列表表示逐字段一致。
        """
        self.set_profile(chan, profile)
        back = self.get_profile(chan)
        return P.diff(profile, back)

    def set_cont_live(self, chan: int, drv1_lvl: int, drv2_lvl: int,
                      noresp: bool = True) -> None:
        """实时幅度滑条专用（高频）。用 NORESP 时没有应答，出错只会收到 EVT_ERROR。"""
        payload = K.enc_cont_live(drv1_lvl, drv2_lvl)
        if noresp:
            self.t.send_frame(C.CMD_SET_CONT_LIVE, payload, chan=chan, noresp=True)
        else:
            self.t.request(C.CMD_SET_CONT_LIVE, payload, chan=chan, timeout=TIMEOUT_FAST)

    def set_gain(self, chan: int, gain: int) -> None:
        self.t.request(C.CMD_SET_GAIN, K.enc_set_gain(gain), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def set_vbat(self, chan: int, vbat_mode: int, vbat_ref: int) -> None:
        self.t.request(C.CMD_SET_VBAT, K.enc_set_vbat(vbat_mode, vbat_ref),
                       chan=chan, timeout=TIMEOUT_NORMAL)

    def set_ram_params(self, chan: int, params: K.RamParams) -> None:
        self.t.request(C.CMD_SET_RAM_PARAMS, params.encode(), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def set_cont_params(self, chan: int, params: K.ContParams) -> None:
        self.t.request(C.CMD_SET_CONT_PARAMS, params.encode(), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def set_rtp_params(self, chan: int, params: K.RtpParams) -> None:
        self.t.request(C.CMD_SET_RTP_PARAMS, params.encode(), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def rtp_data(self, chan: int, offset: int, data: bytes) -> None:
        self.t.request(C.CMD_RTP_DATA, K.enc_rtp_data(offset, data), chan=chan,
                       timeout=TIMEOUT_SLOW)

    def set_trig_params(self, chan: int, params: K.TrigParams) -> None:
        if not params.trig1_brk or not params.trig1_stop:
            raise DeviceError(C.CMD_SET_TRIG_PARAMS, chan, C.ERR_BAD_PARAM,
                              "TRIG 模式下 TRG1_BRK 与 TRG1_STOP 必须为 1（安全要求，不可关闭）")
        self.t.request(C.CMD_SET_TRIG_PARAMS, params.encode(), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def set_default_profile(self, chan: int) -> None:
        self.t.request(C.CMD_SET_DEFAULT_PROFILE, b"", chan=chan, timeout=TIMEOUT_SLOW)

    # ==================================================================
    # 波形组
    # ==================================================================

    def wave_begin(self, chan: int, sample_rate_code: int, wave_count: int,
                   overwrite: bool = True) -> K.WaveBeginResp:
        resp = self.t.request(C.CMD_WAVE_BEGIN,
                              K.enc_wave_begin(sample_rate_code, wave_count,
                                               1 if overwrite else 0),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return K.WaveBeginResp.decode(resp.payload)

    def wave_add(self, chan: int, wave_id: int, offset: int, total_samples: int,
                 samples: bytes, last: bool = False) -> K.WaveAddResp:
        resp = self.t.request(C.CMD_WAVE_ADD,
                              K.enc_wave_add(wave_id, offset, total_samples, samples, last),
                              chan=chan, timeout=TIMEOUT_WAVE)
        return K.WaveAddResp.decode(resp.payload)

    def wave_commit(self, chan: int, lib_version: int = 1) -> K.WaveCommitResp:
        resp = self.t.request(C.CMD_WAVE_COMMIT, K.u8(lib_version), chan=chan,
                              timeout=TIMEOUT_WAVE)
        return K.WaveCommitResp.decode(resp.payload)

    def wave_abort(self, chan: int) -> None:
        self.t.request(C.CMD_WAVE_ABORT, b"", chan=chan, timeout=TIMEOUT_NORMAL)

    def wave_get_library(self, chan: int) -> K.WaveLibrary:
        resp = self.t.request(C.CMD_WAVE_GET_LIBRARY, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        return K.WaveLibrary.decode(resp.payload)

    def wave_readback(self, chan: int, wave_id: int, offset: int, length: int) -> K.WaveReadbackResp:
        resp = self.t.request(C.CMD_WAVE_READBACK,
                              K.enc_wave_readback(wave_id, offset, length),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return K.WaveReadbackResp.decode(resp.payload)

    def wave_erase(self, chan: int) -> None:
        self.t.request(C.CMD_WAVE_ERASE, b"", chan=chan, timeout=TIMEOUT_SLOW)

    def wave_queue(self, chan: int, ids: Sequence[int]) -> None:
        self.t.request(C.CMD_WAVE_QUEUE, K.enc_wave_queue(ids), chan=chan,
                       timeout=TIMEOUT_NORMAL)

    def wave_gen(self, chan: int, wave_id: int, shape: int, freq_hz: int,
                 amplitude: int, cycles: int, sample_rate_code: int) -> K.WaveGenResp:
        resp = self.t.request(C.CMD_WAVE_GEN,
                              K.enc_wave_gen(wave_id, shape, freq_hz, amplitude,
                                             cycles, sample_rate_code),
                              chan=chan, timeout=TIMEOUT_SLOW)
        return K.WaveGenResp.decode(resp.payload)

    def upload_waveform(self, chan: int, waves: Sequence[Tuple[int, bytes]],
                        sample_rate_code: int = C.RATE_24K, lib_version: int = 1,
                        progress: Optional[Callable[[int, int], None]] = None
                        ) -> K.WaveCommitResp:
        """把一批波形下发到芯片 SRAM：``WAVE_BEGIN`` → ``WAVE_ADD``×N → ``WAVE_COMMIT``。

        ``waves`` = [(wave_id, samples_signed_int8_bytes), ...]
        ``progress(done_samples, total_samples)`` 每片回调一次。
        """
        if not waves:
            raise ValueError("至少一条波形")
        begin = self.wave_begin(chan, sample_rate_code, len(waves))
        total_all = sum(len(s) for _i, s in waves)
        if total_all > begin.max_total_samples:
            self.wave_abort(chan)
            raise DeviceError(C.CMD_WAVE_BEGIN, chan, C.ERR_SRAM_OVERFLOW,
                              f"样本总数 {total_all} 超过固件允许的 "
                              f"{begin.max_total_samples}（SRAM 余量 {begin.sram_free}）")
        done = 0
        try:
            for wave_id, samples in waves:
                n = len(samples)
                off = 0
                while off < n:
                    chunk = samples[off:off + C.WAVE_MAX_CHUNK_SAMPLES]
                    last = (off + len(chunk)) >= n
                    self.wave_add(chan, wave_id, off, n, chunk, last)
                    off += len(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total_all)
            return self.wave_commit(chan, lib_version)
        except Exception:
            try:
                self.wave_abort(chan)
            except Exception:
                pass
            raise

    def verify_waveform(self, chan: int, waves: Sequence[Tuple[int, bytes]]
                        ) -> List[Tuple[int, int, int, int]]:
        """``WAVE_READBACK`` 逐段读回比对。

        返回 [(wave_id, offset, 本地值, 设备值)]，空列表表示完全一致。
        """
        bad: List[Tuple[int, int, int, int]] = []
        for wave_id, samples in waves:
            n = len(samples)
            off = 0
            while off < n:
                want = min(C.REG_MAX_LEN, n - off)
                rb = self.wave_readback(chan, wave_id, off, want)
                for i, got in enumerate(rb.samples):
                    idx = off + i
                    if idx >= n:
                        break
                    if got != samples[idx]:
                        bad.append((wave_id, idx, samples[idx], got))
                off += want
        return bad

    # ==================================================================
    # 测量组
    # ==================================================================

    def f0_start(self, chan: int, timeout_ms: int = 500, mode: int = 0) -> bool:
        resp = self.t.request(C.CMD_F0_START, K.enc_f0_start(timeout_ms, mode),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return bool(resp.payload and resp.payload[0])

    def f0_result(self, chan: int) -> K.F0Result:
        resp = self.t.request(C.CMD_F0_RESULT, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        return K.F0Result.decode(resp.payload)

    def f0_measure(self, chan: int, timeout_ms: int = 500, mode: int = 0,
                   poll_s: float = 0.1) -> K.F0Result:
        """启动 F0 检测并轮询到有效结果（含超时保护）。"""
        if not self.f0_start(chan, timeout_ms, mode):
            raise DeviceError(C.CMD_F0_START, chan, C.ERR_BUSY, "通道忙或不在线，F0 检测未启动")
        deadline = time.time() + max(1.0, timeout_ms / 1000.0 + 1.0)
        last = K.F0Result()
        while time.time() < deadline:
            last = self.f0_result(chan)
            if last.valid:
                return last
            time.sleep(poll_s)
        return last

    def det_read(self, chan: int, seq: int) -> K.DetReadResp:
        resp = self.t.request(C.CMD_DET_READ, K.enc_det_read(seq), chan=chan,
                              timeout=TIMEOUT_SLOW)
        out = K.DetReadResp.decode(resp.payload)
        return out

    def read_vbat(self, chan: int) -> Optional[float]:
        r = self.det_read(chan, C.DET_SEQ_VBAT)
        return r.value() if r.valid else None

    def read_rl(self, chan: int) -> Optional[float]:
        r = self.det_read(chan, C.DET_SEQ_RL)
        return r.value(self.d2s_gain) if r.valid else None

    def mon_start(self, chan_mask: int, period_ms: int, fields: int) -> None:
        self.t.request(C.CMD_MON_START, K.enc_mon_start(chan_mask, period_ms, fields),
                       timeout=TIMEOUT_NORMAL)

    def mon_stop(self, chan_mask: int) -> None:
        self.t.request(C.CMD_MON_STOP, K.enc_mon_stop(chan_mask), timeout=TIMEOUT_NORMAL)

    def snapshot(self, chan: int) -> K.Snapshot:
        resp = self.t.request(C.CMD_SNAPSHOT, b"", chan=chan, timeout=TIMEOUT_NORMAL)
        return K.Snapshot.decode(resp.payload)

    # ==================================================================
    # 测试组
    # ==================================================================

    def sweep_amp(self, chan: int, mode: int, from_lvl: int, to_lvl: int, step: int,
                  dwell_ms: int = 200, settle_ms: int = 100) -> K.SweepResp:
        if step == 0:
            raise DeviceError(C.CMD_SWEEP_AMP, chan, C.ERR_BAD_PARAM, "step 不可为 0")
        resp = self.t.request(C.CMD_SWEEP_AMP,
                              K.enc_sweep_amp(mode, from_lvl, to_lvl, step,
                                              dwell_ms, settle_ms),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return K.SweepResp.decode(resp.payload)

    def sweep_freq(self, chan: int, from_hz: int, to_hz: int, step_hz: int,
                   dwell_ms: int = 200) -> K.SweepResp:
        if step_hz == 0:
            raise DeviceError(C.CMD_SWEEP_FREQ, chan, C.ERR_BAD_PARAM, "step_hz 不可为 0")
        resp = self.t.request(C.CMD_SWEEP_FREQ,
                              K.enc_sweep_freq(from_hz, to_hz, step_hz, dwell_ms),
                              chan=chan, timeout=TIMEOUT_NORMAL)
        return K.SweepResp.decode(resp.payload)

    def sweep_abort(self) -> None:
        self.t.request(C.CMD_SWEEP_ABORT, b"", timeout=TIMEOUT_NORMAL)

    def test_run(self, test_id: int, params: bytes = b"") -> bool:
        resp = self.t.request(C.CMD_TEST_RUN, K.enc_test_run(test_id, params),
                              timeout=TIMEOUT_NORMAL)
        return bool(resp.payload and resp.payload[0])

    def test_abort(self) -> None:
        self.t.request(C.CMD_TEST_ABORT, b"", timeout=TIMEOUT_NORMAL)

    def selftest(self) -> bool:
        resp = self.t.request(C.CMD_SELFTEST, b"", timeout=TIMEOUT_NORMAL)
        return bool(resp.payload and resp.payload[0])

    # ==================================================================
    # 安全辅助
    # ==================================================================

    def count_full_amplitude(self, chan_mask: int) -> int:
        """统计掩码里"满幅"的通道数（协议 §11 判定阈值）。"""
        n = 0
        for ch in range(C.CHANNEL_COUNT):
            if not (chan_mask & (1 << ch)):
                continue
            prof = self.profiles.get(ch)
            if prof is None:
                continue
            if prof.mode == C.MODE_CONT and prof.drv1_lvl > C.FULL_AMP_CONT_DRV1_LVL:
                n += 1
            elif prof.mode in (C.MODE_RAM, C.MODE_RTP) and prof.gain > C.FULL_AMP_RAM_GAIN:
                n += 1
            elif prof.mode == C.MODE_TRIG and prof.trg_gain > C.FULL_AMP_RAM_GAIN:
                n += 1
        return n

    def safety_check(self, chan_mask: int, override: bool = False) -> Tuple[bool, str]:
        """播放前的本地预检。返回 (是否允许, 说明)。"""
        mask = chan_mask & 0xFF
        if not mask:
            return False, "没有选中任何通道"
        try:
            cfg = self.get_safety()
        except Exception:
            return True, "（未能读取安全参数，交由固件判定）"
        n_full = self.count_full_amplitude(mask)
        if n_full > cfg.max_simul_full_amp and not override:
            return False, (f"选中通道中有 {n_full} 路满幅，超过限制 "
                           f"{cfg.max_simul_full_amp}。固件将返回 ERR_SAFETY_LIMIT。"
                           f"可降低幅度，或显式使用 override（危险）。")
        return True, ""


# --------------------------------------------------------------------------
# 便捷构造
# --------------------------------------------------------------------------


def open_device(port: str, **kwargs) -> Tuple[AWTestDevice, ConnectionInfo]:
    dev = AWTestDevice()
    info = dev.connect(port, **kwargs)
    return dev, info
