# -*- coding: utf-8 -*-
"""一个**纯软件**的假设备传输层，用于在没有硬件的情况下测试上位机。

它实现了 `SerialTransport` 的对外接口（`is_open` / `request` / `send_frame` /
`start_heartbeat` / `stop_all` / `trig_park` / `enter_binary_mode` / `on_event` / `stats`），
并按协议语义生成应答，因此可以端到端地验证 `device.py`、脚本引擎与界面逻辑。

不是"记录调用的 mock"，而是一个**行为模型**：会记账、会按安全规则拒绝请求。
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

from awtest import profiles as P
from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.protocol.framing import (CHAN_ALL, CHAN_NONE, FLAG_ERROR,
                                     FLAG_RESPONSE, Frame)
from awtest.protocol.transport import DeviceError, RequestTimeout, Stats


class FakeDeviceTransport:
    """行为级的假设备。"""

    def __init__(self, present_mask: int = 0xFF, with_tca: bool = True,
                 caps: Optional[int] = None, internal_pullup: bool = False):
        self._open = False
        self.binary = False
        self.stats = Stats()
        self.on_event = None
        self.on_frame = None
        self.on_log = None
        self.on_ascii = None
        self.on_disconnect = None

        self.present_mask = present_mask
        self.with_tca = with_tca
        self.internal_pullup = internal_pullup
        self.caps = caps if caps is not None else (
            C.CAPS_EXPECTED_BOARD_A | (C.CAP_INTERNAL_PULLUP if internal_pullup else 0))
        self.heartbeat_period: Optional[float] = None

        # 设备内部状态
        self.regs: Dict[Tuple[int, int], int] = {}
        self.profiles: Dict[int, P.Profile] = {
            ch: P.Profile() for ch in range(C.CHANNEL_COUNT)
        }
        self.safety = K.SafetyCfg()
        self.log_level = C.LOG_WARN
        self.mode: Dict[int, int] = {ch: C.MODE_RAM for ch in range(C.CHANNEL_COUNT)}
        self.playing: Dict[int, bool] = {ch: False for ch in range(C.CHANNEL_COUNT)}
        self.last_err = 0
        self.safety_state = C.SAFETY_NORMAL
        self.wave_lib: Dict[int, Dict[int, bytes]] = {}
        self.wave_session: Optional[int] = None
        self.f0_result = K.F0Result(valid=1, mode=0, raw_h=0x0B, raw_l=0x70,
                                    f0_hz_x10=0x0DAE)
        self.avg_data = 512
        self.simulate_absent = present_mask != 0xFF
        self.calls: List[Tuple[int, int, bytes]] = []
        self.verify_next_write_mismatch = False
        self._started = time.time()

    # ---- 接口兼容 ----

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, port: str = "FAKE", baudrate: int = 115200, **kw) -> None:
        self._open = True
        self.binary = False

    def close(self, send_stop: bool = False) -> None:
        self._open = False

    def enter_binary_mode(self, timeout: float = 1.0) -> bool:
        self.binary = True
        return True

    def start_heartbeat(self, period_s: float, use_noresp: bool = True) -> None:
        self.heartbeat_period = period_s

    def stop_all(self, immediate: int = 1) -> None:
        self.request(C.CMD_STOP_ALL, bytes([immediate]))

    def trig_park(self) -> None:
        self.request(C.CMD_TRIG_PARK, b"")

    def ping(self) -> Frame:
        return self.request(C.CMD_PING, b"")

    # ---- 请求分发 ----

    def send_frame(self, cmd: int, payload: bytes = b"", chan: int = CHAN_NONE,
                   noresp: bool = False, seq: Optional[int] = None) -> Frame:
        f = Frame.request(cmd, payload, seq=seq if seq is not None else 1,
                          chan=chan, noresp=noresp)
        self.calls.append((cmd, chan, payload))
        if noresp:
            # NORESP 不回应答：把副作用直接执行掉
            self._handle_side_effect(cmd, payload, chan)
        return f

    def request(self, cmd: int, payload: bytes = b"", chan: int = CHAN_NONE,
                timeout: float = 0.5) -> Frame:
        if not self._open:
            raise RequestTimeout(cmd, 1, timeout)
        self.calls.append((cmd, chan, payload))
        self.stats.frames_tx += 1
        seq = self.stats.frames_tx & 0xFF
        out = self._dispatch(cmd, payload, chan)
        if isinstance(out, tuple):
            errcode, text = out
            self.stats.device_errors += 1
            raise DeviceError(cmd, chan, errcode, text)
        self.stats.frames_rx += 1
        return Frame.response(cmd, out, seq=seq, chan=chan)

    # ---- 各命令的行为 ----

    def _handle_side_effect(self, cmd: int, payload: bytes, chan: int) -> None:
        if cmd == C.CMD_SET_CONT_LIVE and len(payload) >= 2:
            prof = self.profiles.get(chan)
            if prof is not None:
                prof.drv1_lvl, prof.drv2_lvl = payload[0], payload[1]

    def _dispatch(self, cmd: int, p: bytes, chan: int):
        now = int((time.time() - self._started) * 1000)

        if cmd == C.CMD_PING:
            return K.PingResp(1, 1, 0, 0, C.BOARD_ID_VIBRATION_TEST_BOARD_A, now).encode()

        if cmd == C.CMD_GET_CAPS:
            return K.Caps(C.CHANNEL_COUNT, 200, self.caps).encode()

        if cmd == C.CMD_GET_STATUS:
            return K.Status(now, self.safety_state, 0, self.last_err, 10).encode()

        if cmd == C.CMD_GET_SAFETY:
            return self.safety.encode()
        if cmd == C.CMD_SET_SAFETY:
            cfg = K.SafetyCfg.decode(p)
            if not (50 <= cfg.play_timeout_ms <= 60000 and
                    1 <= cfg.max_simul_full_amp <= 8 and
                    200 <= cfg.heartbeat_timeout_ms <= 60000):
                return (C.ERR_BAD_PARAM, "安全参数越界")
            self.safety = cfg
            return b""
        if cmd == C.CMD_CLEAR_ERRORS:
            self.last_err = 0
            self.safety_state = C.SAFETY_NORMAL
            return b""
        if cmd == C.CMD_SET_LOG_LEVEL:
            self.log_level = p[0]
            return b""
        if cmd == C.CMD_MCU_RESET:
            return b""
        if cmd == C.CMD_SET_PROTO_MODE:
            return b""

        # ---- 总线 ----
        if cmd == C.CMD_BUS_PROBE:
            if not self.with_tca:
                return (C.ERR_TCA_NOT_FOUND, "0x70 无应答")
            return K.BusProbeResp(1, 6, 7, 400000, int(self.internal_pullup), 1,
                                  0x70, 0).encode()
        if cmd == C.CMD_BUS_SET_SPEED:
            hz = struct.unpack("<I", p[:4])[0]
            if hz not in (100000, 400000):
                return (C.ERR_BAD_PARAM, "只接受 100000 或 400000")
            return struct.pack("<I", hz)
        if cmd == C.CMD_BUS_SET_PULLUP:
            self.internal_pullup = bool(p[0])
            if self.internal_pullup:
                self.caps |= C.CAP_INTERNAL_PULLUP
            else:
                self.caps &= ~C.CAP_INTERNAL_PULLUP
            return b""
        if cmd == C.CMD_BUS_RECOVER:
            return bytes([3])
        if cmd == C.CMD_TCA_SET_MASK:
            if p[0] not in (0x00,) and bin(p[0]).count("1") != 1:
                return (C.ERR_BAD_PARAM, "mask 只允许 0x00 或恰好一个 bit")
            return bytes([p[0]])
        if cmd == C.CMD_TCA_GET_MASK:
            return bytes([0])
        if cmd == C.CMD_TCA_RESET:
            return b""
        if cmd == C.CMD_TCA_READ_CTRL:
            return bytes([0])

        # ---- 通道 ----
        if cmd == C.CMD_CH_PROBE:
            if not self._present(chan):
                return K.ChProbeResp(0, 0, 0, 0).encode()
            return K.ChProbeResp(1, 0x23, 0x40, 1).encode()
        if cmd == C.CMD_CH_PROBE_ALL:
            entries = []
            for ch in range(C.CHANNEL_COUNT):
                if self._present(ch):
                    entries.append((ch, 0x23, 0x40, 0))
                else:
                    entries.append((ch, 0, 0, C.ERR_NOT_PRESENT))
            return K.ChProbeAllResp(self.present_mask, C.CHANNEL_COUNT, entries).encode()
        if cmd == C.CMD_CH_GET_INFO:
            prof = self.profiles[chan]
            return K.ChInfo(int(self._present(chan)), 0x23, 0x40, self.mode[chan],
                            int(self.playing[chan]), 0, 0).encode()

        # ---- 寄存器 ----
        if cmd == C.CMD_REG_READ:
            addr, ln = p[0], p[1]
            if not (1 <= ln <= 64):
                return (C.ERR_BAD_PARAM, "len 1..64")
            data = bytes(self._reg(chan, addr + i) for i in range(ln))
            return data
        if cmd == C.CMD_REG_WRITE:
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            for i, b in enumerate(p[1:]):
                self.regs[(chan, p[0] + i)] = b
            return b""
        if cmd == C.CMD_REG_UPDATE:
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            addr, mask, val = p[0], p[1], p[2]
            old = self._reg(chan, addr)
            new = (old & ~mask & 0xFF) | (val & mask)
            self.regs[(chan, addr)] = new
            if self.verify_next_write_mismatch:
                self.verify_next_write_mismatch = False
                return K.RegUpdateResp(old, (new + 1) & 0xFF).encode()
            return K.RegUpdateResp(old, new).encode()
        if cmd == C.CMD_REG_READ_MULTI:
            out = bytearray()
            i = 1
            for _ in range(p[0]):
                addr, ln = p[i], p[i + 1]
                i += 2
                out += bytes(self._reg(chan, addr + k) for k in range(ln))
            return bytes(out)
        if cmd == C.CMD_REG_DUMP:
            start, ln = p[0], p[1]
            return bytes([start]) + bytes(self._reg(chan, start + i) for i in range(ln))
        if cmd == C.CMD_STREAM_WRITE:
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            addr, ln = p[0], p[3]
            for i in range(ln):
                self.regs[(chan, addr)] = p[4 + i]
            return b""

        # ---- 器件 ----
        if cmd in (C.CMD_AW_SOFTRST, C.CMD_AW_WAKE, C.CMD_AW_STANDBY):
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            if cmd == C.CMD_AW_SOFTRST:
                # 软复位清掉全部配置寄存器，但固件会重放 Profile
                self._write_profile_regs(chan, self.profiles[chan])
            return b""
        if cmd == C.CMD_AW_INIT_DEFAULT:
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            self.profiles[chan] = P.Profile()
            self._write_profile_regs(chan, self.profiles[chan])
            return K.InitDefaultResp(6, 6).encode()
        if cmd == C.CMD_AW_GET_STATE:
            return K.AwState(0 if not self.playing[chan] else 7, 0, 0x08,
                             C.MODE_UNKNOWN if not self.playing[chan] else self.mode[chan],
                             int(self._present(chan))).encode()

        # ---- 播放 ----
        if cmd == C.CMD_SET_MODE:
            if self.mode[chan] == C.MODE_TRIG and not (self._reg(chan, 0x3A) & 0x04):
                return (C.ERR_VERIFY_FAILED, "0x3A bit2 = 0，拒绝进入 TRIG")
            self.mode[chan] = p[0]
            return b""
        if cmd == C.CMD_PLAY:
            if chan >= C.CHANNEL_COUNT:
                return (C.ERR_BAD_CHANNEL, "通道号须在 0..7")
            override = p[2] if len(p) >= 3 else 0
            allow, why, n_full = self._check_full_amp(1 << chan, bool(override))
            if not allow:
                return (C.ERR_SAFETY_LIMIT, why)
            to = struct.unpack("<H", p[:2])[0] or self.safety.play_timeout_ms
            self.playing[chan] = True
            # 安全超时（很短）→ 上报 reason=2；正常播放 → reason=0
            if to < 500:
                self._schedule_play_done(chan, to / 1000.0, 2)
            else:
                self._schedule_play_done(chan, min(to / 1000.0, 0.3), 0)
            return struct.pack("<H", to)
        if cmd == C.CMD_STOP:
            self.playing[chan] = False
            return b""
        if cmd == C.CMD_PLAY_MULTI:
            mask, to, skew = p[0], struct.unpack("<H", p[1:3])[0], struct.unpack("<H", p[3:5])[0]
            override = bool(p[5])
            allow, why, _ = self._check_full_amp(mask, override)
            if not allow:
                return (C.ERR_SAFETY_LIMIT, why)
            started = 0
            for ch in range(C.CHANNEL_COUNT):
                if mask & (1 << ch) and self._present(ch):
                    self.playing[ch] = True
                    started |= 1 << ch
            return K.PlayMultiResp(started, skew).encode()
        if cmd == C.CMD_TRIG_PULSE:
            mask = p[0]
            expanded = C.expand_shared_trig_mask(mask)
            allow, why, _ = self._check_full_amp(expanded, False)
            if not allow:
                return (C.ERR_SAFETY_LIMIT, why)
            started = 0
            for ch in range(C.CHANNEL_COUNT):
                if expanded & (1 << ch) and self._present(ch):
                    self.playing[ch] = True
                    started |= 1 << ch
            return bytes([started])
        if cmd in (C.CMD_TRIG_LEVEL, C.CMD_TRIG_PATTERN):
            return b""
        if cmd == C.CMD_GET_PLAY_STATE:
            return K.PlayState(int(self.playing[chan]), 0 if not self.playing[chan] else 7,
                               12).encode()
        if cmd == C.CMD_STOP_ALL:
            stopped = 0
            for ch in range(C.CHANNEL_COUNT):
                if self.playing[ch]:
                    stopped |= 1 << ch
                self.playing[ch] = False
            return bytes([stopped])
        if cmd == C.CMD_TRIG_PARK:
            return b""

        # ---- 参数 ----
        if cmd == C.CMD_SET_PROFILE:
            prof = P.Profile.from_bytes(p)
            problems = prof.validate()
            if problems:
                return (C.ERR_BAD_PARAM, f"{problems[0][0]}：{problems[0][1]}")
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            self.profiles[chan] = prof
            self.mode[chan] = prof.mode
            self._write_profile_regs(chan, prof)
            return K.SetProfileResp(0, 0).encode()
        if cmd == C.CMD_GET_PROFILE:
            if not self._present(chan):
                return (C.ERR_NOT_PRESENT, "该通道无器件应答")
            return self.profiles[chan].to_bytes()
        if cmd == C.CMD_SET_CONT_LIVE:
            if not (0 <= p[0] <= 127 and 0 <= p[1] <= 127):
                return (C.ERR_BAD_PARAM, "DRV*_LVL 0..127")
            prof = self.profiles[chan]
            prof.drv1_lvl, prof.drv2_lvl = p[0], p[1]
            return b""
        if cmd == C.CMD_SET_GAIN:
            self.profiles[chan].gain = p[0]
            return b""
        if cmd == C.CMD_SET_VBAT:
            self.profiles[chan].vbat_mode, self.profiles[chan].vbat_ref = p[0], p[1]
            return b""
        if cmd == C.CMD_SET_RAM_PARAMS:
            prof = self.profiles[chan]
            prof.wavseq = list(p[0:8])
            prof.mainloop, prof.waitslot = p[8], p[9]
            prof.seq_loop = list(p[10:14])
            return b""
        if cmd == C.CMD_SET_CONT_PARAMS:
            cp = K.ContParams.decode(p)
            prof = self.profiles[chan]
            for f in ("en_f0_det", "sin_mode", "edge_fre", "f_pre", "drv_width",
                      "wait_num", "brk_gain", "track_en", "drv1_lvl", "drv2_lvl",
                      "drv1_time", "drv2_time", "brk_time", "track_margin"):
                setattr(prof, f, getattr(cp, f))
            return b""
        if cmd == C.CMD_SET_RTP_PARAMS:
            if p[0] == C.RATE_48K:
                return (C.ERR_UNSUPPORTED, "48K 本板不可用")
            rp = K.RtpParams.decode(p)
            prof = self.profiles[chan]
            prof.wavdat_mode, prof.rtp_flags = rp.wavdat_mode, rp.auto_dynamic_sin
            prof.base_addr, prof.fifo_ae, prof.fifo_af = rp.base_addr, rp.fifo_ae, rp.fifo_af
            return b""
        if cmd == C.CMD_RTP_DATA:
            return b""
        if cmd == C.CMD_SET_TRIG_PARAMS:
            tp = K.TrigParams.decode(p)
            if not tp.trig1_brk or not tp.trig1_stop:
                return (C.ERR_BAD_PARAM, "TRG1_BRK/TRG1_STOP 必须为 1")
            if tp.trig1_mode != 3:
                return (C.ERR_BAD_PARAM, "trig1_mode 必须为 3")
            prof = self.profiles[chan]
            for f in ("trig1_pos_en", "trig1_seq_p", "trig1_neg_en", "trig1_seq_n",
                      "trig1_polar", "trig1_lev", "trig1_brk", "trig1_stop",
                      "trig1_prio", "trg_gain", "trig1_mode", "intn_pin", "start_dly"):
                setattr(prof, f, getattr(tp, f))
            return b""
        if cmd == C.CMD_SET_DEFAULT_PROFILE:
            self.profiles[chan] = P.Profile()
            return b""

        # ---- 波形 ----
        if cmd == C.CMD_WAVE_BEGIN:
            if p[0] == C.RATE_48K:
                return (C.ERR_UNSUPPORTED, "48K 本板不可用")
            self.wave_session = (self.wave_session or 0) + 1
            self.wave_lib[chan] = {}
            base = self.profiles[chan].base_addr
            max_total = max(0, 3072 - base - 1 - 4 * p[1])
            return K.WaveBeginResp(self.wave_session, max_total, 3072 - base).encode()
        if cmd == C.CMD_WAVE_ADD:
            if self.wave_session is None:
                return (C.ERR_NOT_ARMED, "请先 WAVE_BEGIN")
            wave_id = p[0]
            buf = self.wave_lib.setdefault(chan, {})
            prev = buf.get(wave_id, b"")
            offset = struct.unpack("<H", p[1:3])[0]
            if offset != len(prev):
                return (C.ERR_BAD_PARAM, "分片不连续")
            buf[wave_id] = prev + p[6:]
            return K.WaveAddResp(wave_id, offset).encode()
        if cmd == C.CMD_WAVE_COMMIT:
            if self.wave_session is None:
                return (C.ERR_NOT_ARMED, "请先 WAVE_BEGIN")
            buf = self.wave_lib.get(chan, {})
            base = self.profiles[chan].base_addr
            total = 1 + 4 * len(buf) + sum(len(v) for v in buf.values())
            if total > 3072 - base:
                return (C.ERR_SRAM_OVERFLOW, "波形库超过 SRAM 容量")
            entries = []
            off = 1 + 4 * len(buf)
            for wid in sorted(buf):
                start = base + off
                off += len(buf[wid])
                entries.append(K.WaveLibEntry(wid, start, base + off - 1))
            self.wave_session = None
            self._lib_entries = entries
            return K.WaveCommitResp(0, total, 0x1234, base, entries).encode()
        if cmd == C.CMD_WAVE_ABORT:
            self.wave_session = None
            return b""
        if cmd == C.CMD_WAVE_ERASE:
            self.wave_lib[chan] = {}
            self._lib_entries = []
            return b""
        if cmd == C.CMD_WAVE_GET_LIBRARY:
            entries = getattr(self, "_lib_entries", [])
            return K.WaveLibrary(1, 0, self.profiles[chan].base_addr, entries).encode()
        if cmd == C.CMD_WAVE_READBACK:
            wid, offset, ln = p[0], struct.unpack("<H", p[1:3])[0], p[3]
            if not hasattr(self, "_lib_entries"):
                return (C.ERR_BAD_PARAM, "库为空")
            data = self.wave_lib.get(chan, {}).get(wid, b"")
            if offset + ln > len(data):
                return (C.ERR_BAD_PARAM, "超出波形长度")
            return K.WaveReadbackResp(wid, offset, data[offset:offset + ln]).encode()
        if cmd == C.CMD_WAVE_QUEUE:
            self.profiles[chan].wavseq = list(p[1:1 + p[0]]) + [0] * (8 - p[0])
            return b""
        if cmd == C.CMD_WAVE_GEN:
            # 协议 §9.8：WAVE_GEN 只能「加入当前会话」，必须先 WAVE_BEGIN
            if self.wave_session is None:
                return (C.ERR_NOT_ARMED, "请先 WAVE_BEGIN")
            wave_id, shape = p[0], p[1]
            freq = struct.unpack("<H", p[2:4])[0]
            amp = p[4]
            cycles = struct.unpack("<H", p[5:7])[0]
            rate = p[7]
            # 用真实的波形生成模块合成样本，行为与固件 wave_gen_samples() 一致
            from awtest.waveform import generator as G
            samples = G.generate(shape, freq, amp, cycles, C.RATE_HZ.get(rate, 24000))
            data = G.to_bytes(samples)
            self.wave_lib.setdefault(chan, {})[wave_id] = data
            return K.WaveGenResp(wave_id, len(data)).encode()

        # ---- 测量 ----
        if cmd == C.CMD_F0_START:
            if not self._present(chan):
                return bytes([0])
            return bytes([1])
        if cmd == C.CMD_F0_RESULT:
            self.f0_result.mode = p[0] if p else 0
            return self.f0_result.encode()
        if cmd == C.CMD_DET_READ:
            seq = p[0]
            avg = self.avg_data
            if seq == C.DET_SEQ_VBAT:
                vx = int(K.DetReadResp.vbat_v(avg) * 100)
            elif seq == C.DET_SEQ_RL:
                vx = int(K.DetReadResp.rl_ohm(avg, 8) * 100)
            else:
                vx = avg
            return K.DetReadResp(avg, avg, vx, 1, seq).encode()
        if cmd in (C.CMD_MON_START, C.CMD_MON_STOP):
            return b""
        if cmd == C.CMD_SNAPSHOT:
            return K.Snapshot(int(self._present(chan)), 0x23, 0x40, self.mode[chan],
                              int(self.playing[chan]), 0, 0, 0x08, 0x0B, 0x70,
                              0x0B, 0x70, 0x02, avg_lo(self.avg_data),
                              avg_hi(self.avg_data)).encode()

        # ---- 测试 ----
        if cmd == C.CMD_SWEEP_AMP:
            mode, a, b, step = p[0], p[1], p[2], p[3]
            if step == 0:
                return (C.ERR_BAD_PARAM, "step 不可为 0")
            total = abs(b - a) // step + 1
            return K.SweepResp(1, min(total, 255)).encode()
        if cmd == C.CMD_SWEEP_FREQ:
            f, t, s = struct.unpack("<HHH", p[:6])
            if s == 0:
                return (C.ERR_BAD_PARAM, "step_hz 不可为 0")
            return K.SweepResp(1, min(abs(t - f) // s + 1, 255)).encode()
        if cmd in (C.CMD_SWEEP_ABORT, C.CMD_TEST_ABORT):
            return b""
        if cmd == C.CMD_TEST_RUN:
            return K.AcceptedResp(1).encode()
        if cmd == C.CMD_SELFTEST:
            return K.AcceptedResp(1).encode()

        return (C.ERR_UNKNOWN_CMD, f"未实现的命令 0x{cmd:02X}")

    # ---- 内部辅助 ----

    def _present(self, chan: int) -> bool:
        return bool(self.present_mask & (1 << chan))

    def _reg(self, chan: int, addr: int) -> int:
        if (chan, addr) in self.regs:
            return self.regs[(chan, addr)]
        # 未显式写过的寄存器：给出与真实芯片一致的读数（CHIPID / LDO_OK / GLB_STATE…）
        fixed = {
            0x57: 0x23,      # CHIPID_H
            0x69: 0x40,      # CHIPID_L
            0x04: 0x08,      # SYSST2 bit3 LDO_OK = 1
            0x3F: 0x00 if not self.playing[chan] else (0x06 | self.mode[chan]),
            0x01: 0x00,      # SYSST
        }
        if addr in fixed:
            return fixed[addr]
        return 0

    # ---- 播放结束事件（模拟固件的轮询上报） ----

    def _schedule_play_done(self, ch: int, delay_s: float, reason: int) -> None:
        token = self._play_token = getattr(self, "_play_token", 0) + 1

        def fire() -> None:
            if token != getattr(self, "_play_token", 0):
                return
            if not self.playing.get(ch):
                return
            self.playing[ch] = False
            self.emit(C.EVT_PLAY_DONE,
                      bytes([reason]) + struct.pack("<H", int(delay_s * 1000)),
                      chan=ch)

        t = threading.Timer(max(0.02, delay_s), fire)
        t.daemon = True
        t.start()

    def _write_profile_regs(self, chan: int, prof: P.Profile) -> None:
        self.regs[(chan, 0x07)] = prof.gain
        self.regs[(chan, 0x08)] = (prof.mode & 3) | (prof.brk_en << 2) | (prof.stop_mode << 3)
        for i, v in enumerate(prof.wavseq):
            self.regs[(chan, 0x0A + i)] = v
        self.regs[(chan, 0x17)] = ((prof.en_f0_det << 5) | (prof.sin_mode << 4) |
                                   prof.edge_fre)
        self.regs[(chan, 0x18)] = prof.f_pre
        self.regs[(chan, 0x1C)] = (prof.track_en << 7) | prof.drv1_lvl
        self.regs[(chan, 0x1D)] = prof.drv2_lvl
        self.regs[(chan, 0x33)] = (prof.trig1_pos_en << 7) | prof.trig1_seq_p
        self.regs[(chan, 0x39)] = ((prof.trig1_polar << 5) | (prof.trig1_lev << 4) |
                                   (prof.trig1_brk << 3))
        self.regs[(chan, 0x3A)] = (prof.trig1_stop << 2)
        self.regs[(chan, 0x45)] = ((prof.trig1_mode << 4) | (prof.en_fir << 1) |
                                   prof.intn_pin)

    def _check_full_amp(self, mask: int, override: bool) -> Tuple[bool, str, int]:
        n = 0
        for ch in range(C.CHANNEL_COUNT):
            if not (mask & (1 << ch)):
                continue
            prof = self.profiles.get(ch)
            if prof is None:
                continue
            if prof.mode == C.MODE_CONT and prof.drv1_lvl > C.FULL_AMP_CONT_DRV1_LVL:
                n += 1
            elif prof.mode in (C.MODE_RAM, C.MODE_RTP) and prof.gain > C.FULL_AMP_RAM_GAIN:
                n += 1
        if n > self.safety.max_simul_full_amp and not override:
            return False, f"{n} 路满幅超过限制 {self.safety.max_simul_full_amp}", n
        return True, "", n

    # ---- 测试辅助：制造事件 ----

    def emit(self, cmd: int, payload: bytes = b"", chan: int = CHAN_NONE) -> None:
        if self.on_event:
            self.on_event(Frame.event(cmd, payload, chan=chan))


def avg_hi(avg: int) -> int:
    """Snapshot.reg51 的 bit1:0 是 AVG_DATA 高位。"""
    return (avg >> 8) & 0x03


def avg_lo(avg: int) -> int:
    return avg & 0xFF
