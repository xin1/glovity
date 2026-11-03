# -*- coding: utf-8 -*-
"""各命令 payload 的编解码（dataclass，不含裸 struct 调用散落各处）。

每条命令/事件的载荷布局严格取自 ``03_串口协议规范_v1.0.md`` §9 与 §10。
所有多字节整数为 **little-endian**，长度不含 8 字节头与 2 字节 CRC。

本模块**不依赖任何 GUI 模块**，可单独 pytest。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import commands as C
from .framing import CHAN_ALL, CHAN_NONE

# --------------------------------------------------------------------------
# 极小的读写助手：所有解码都带长度校验，越界抛 ValueError
# --------------------------------------------------------------------------


class PayloadError(ValueError):
    """载荷长度或内容不符合协议。"""


class _R:
    """顺序读取器。"""

    __slots__ = ("b", "i")

    def __init__(self, data: bytes):
        self.b = bytes(data)
        self.i = 0

    def u8(self) -> int:
        if self.i + 1 > len(self.b):
            raise PayloadError(f"载荷不足：需要 1 字节 @{self.i}，实际 {len(self.b)}")
        v = self.b[self.i]
        self.i += 1
        return v

    def u16(self) -> int:
        if self.i + 2 > len(self.b):
            raise PayloadError(f"载荷不足：需要 2 字节 @{self.i}，实际 {len(self.b)}")
        v = struct.unpack_from("<H", self.b, self.i)[0]
        self.i += 2
        return v

    def u32(self) -> int:
        if self.i + 4 > len(self.b):
            raise PayloadError(f"载荷不足：需要 4 字节 @{self.i}，实际 {len(self.b)}")
        v = struct.unpack_from("<I", self.b, self.i)[0]
        self.i += 4
        return v

    def raw(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise PayloadError(f"载荷不足：需要 {n} 字节 @{self.i}，实际 {len(self.b)}")
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def rest(self) -> bytes:
        v = self.b[self.i:]
        self.i = len(self.b)
        return v

    def text(self) -> str:
        return self.rest().decode("utf-8", errors="replace")

    def done(self, exact: bool = True, allow_longer: bool = True) -> None:
        if exact and self.i != len(self.b) and not allow_longer:
            raise PayloadError(f"载荷有多余 {len(self.b) - self.i} 字节")


def u8(v: int) -> bytes:
    return struct.pack("<B", v & 0xFF)


def u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise PayloadError(msg)


# ==========================================================================
# §9.1 系统组
# ==========================================================================


@dataclass
class PingResp:
    """0x01 PING 应答：proto_ver(1) fw_major(1) fw_minor(1) fw_patch(1) board_id(1) uptime_ms(4)"""

    proto_ver: int = 1
    fw_major: int = 0
    fw_minor: int = 0
    fw_patch: int = 0
    board_id: int = C.BOARD_ID_VIBRATION_TEST_BOARD_A
    uptime_ms: int = 0

    LEN = 9

    @property
    def fw_version(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}.{self.fw_patch}"

    @classmethod
    def decode(cls, p: bytes) -> "PingResp":
        r = _R(p)
        o = cls(r.u8(), r.u8(), r.u8(), r.u8(), r.u8(), r.u32())
        return o

    def encode(self) -> bytes:
        return bytes([self.proto_ver, self.fw_major, self.fw_minor,
                      self.fw_patch, self.board_id]) + u32(self.uptime_ms)


@dataclass
class Caps:
    """0x02 GET_CAPS 应答：channel_count(1) max_payload(1) caps(4 LE)"""

    channel_count: int = C.CHANNEL_COUNT
    max_payload: int = 200
    caps: int = 0

    LEN = 6

    @classmethod
    def decode(cls, p: bytes) -> "Caps":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u32())

    def encode(self) -> bytes:
        return bytes([self.channel_count, self.max_payload]) + u32(self.caps)

    def has(self, bit: int) -> bool:
        return bool(self.caps & bit)

    def cap_report(self) -> List[Tuple[int, str, str, bool]]:
        """[(位, 名称, 说明, 是否可用)]，界面能力清单直接用。"""
        return [(bit, label, note, bool(self.caps & bit))
                for bit, label, note in C.CAP_LABELS]


@dataclass
class Status:
    """0x03 GET_STATUS 应答：uptime_ms(4) safety_state(1) active_mask(1) last_err(1) heartbeat_age_ms(2)"""

    uptime_ms: int = 0
    safety_state: int = C.SAFETY_NORMAL
    active_mask: int = 0
    last_err: int = 0
    heartbeat_age_ms: int = 0

    LEN = 9

    @classmethod
    def decode(cls, p: bytes) -> "Status":
        r = _R(p)
        return cls(r.u32(), r.u8(), r.u8(), r.u8(), r.u16())

    def encode(self) -> bytes:
        return (u32(self.uptime_ms) + bytes([self.safety_state, self.active_mask,
                                             self.last_err]) + u16(self.heartbeat_age_ms))

    @property
    def safety_state_name(self) -> str:
        return C.SAFETY_STATE_NAMES.get(self.safety_state, "未知")


@dataclass
class SafetyCfg:
    """0x07 SET_SAFETY / 0x08 GET_SAFETY：play_timeout_ms(2) max_simul_full_amp(1) heartbeat_timeout_ms(2) flags(1)"""

    play_timeout_ms: int = C.DEFAULT_PLAY_TIMEOUT_MS
    max_simul_full_amp: int = C.DEFAULT_MAX_SIMUL_FULL_AMP
    heartbeat_timeout_ms: int = C.DEFAULT_HEARTBEAT_TIMEOUT_MS
    flags: int = 0

    LEN = 6

    FLAG_ALLOW_OVERRIDE = 0x01
    FLAG_LOCK_ON_BUS_ERROR = 0x02

    @classmethod
    def decode(cls, p: bytes) -> "SafetyCfg":
        r = _R(p)
        return cls(r.u16(), r.u8(), r.u16(), r.u8())

    def encode(self) -> bytes:
        return u16(self.play_timeout_ms) + u8(self.max_simul_full_amp) + \
            u16(self.heartbeat_timeout_ms) + u8(self.flags)


# ==========================================================================
# §9.2 总线组
# ==========================================================================


@dataclass
class BusProbeResp:
    """0x10 BUS_PROBE 应答（12 字节）"""

    sel_used: int = 0
    sda_gpio: int = 0
    scl_gpio: int = 0
    speed_hz: int = 0
    internal_pullup: int = 0
    tca_present: int = 0
    tca_addr: int = C.TCA_I2C_ADDR
    ctrl_readback: int = 0

    LEN = 12

    BUS_AUTO = 0
    BUS_LP = 1
    BUS_HP = 2

    @property
    def bus_name(self) -> str:
        return {1: "LP", 2: "HP"}.get(self.sel_used, "未知")

    @classmethod
    def decode(cls, p: bytes) -> "BusProbeResp":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u32(), r.u8(), r.u8(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return (bytes([self.sel_used, self.sda_gpio, self.scl_gpio]) + u32(self.speed_hz)
                + bytes([self.internal_pullup, self.tca_present, self.tca_addr,
                         self.ctrl_readback]))


# ==========================================================================
# §9.3 通道组
# ==========================================================================


@dataclass
class ChProbeResp:
    """0x18 CH_PROBE 应答（4 字节）"""

    present: int = 0
    chipid_h: int = 0
    chipid_l: int = 0
    vreg_ok: int = 0

    LEN = 4

    @property
    def chipid(self) -> int:
        return (self.chipid_h << 8) | self.chipid_l

    @property
    def chipid_ok(self) -> bool:
        return self.chipid == C.AW_CHIPID_EXPECTED

    @classmethod
    def decode(cls, p: bytes) -> "ChProbeResp":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.present, self.chipid_h, self.chipid_l, self.vreg_ok])


@dataclass
class ChProbeAllResp:
    """0x19 CH_PROBE_ALL 应答"""

    present_mask: int = 0
    count: int = 0
    entries: List[Tuple[int, int, int, int]] = field(default_factory=list)  # chan,id_h,id_l,status

    @classmethod
    def decode(cls, p: bytes) -> "ChProbeAllResp":
        r = _R(p)
        mask = r.u8()
        cnt = r.u8()
        entries = []
        for _ in range(cnt):
            entries.append((r.u8(), r.u8(), r.u8(), r.u8()))
        return cls(mask, cnt, entries)

    def encode(self) -> bytes:
        out = bytearray([self.present_mask, len(self.entries)])
        for chan, hid, lid, st in self.entries:
            out += bytes([chan, hid, lid, st])
        return bytes(out)

    def chipid_of(self, chan: int) -> Optional[int]:
        for c, h, l, _st in self.entries:
            if c == chan:
                return (h << 8) | l
        return None


@dataclass
class ChInfo:
    """0x1A CH_GET_INFO 应答（7 字节）"""

    present: int = 0
    chipid_h: int = 0
    chipid_l: int = 0
    mode: int = C.MODE_UNKNOWN
    playing: int = 0
    glb_state: int = 0
    last_err: int = 0

    LEN = 7

    @property
    def chipid(self) -> int:
        return (self.chipid_h << 8) | self.chipid_l

    @classmethod
    def decode(cls, p: bytes) -> "ChInfo":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u8(), r.u8(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.present, self.chipid_h, self.chipid_l, self.mode,
                      self.playing, self.glb_state, self.last_err])


# ==========================================================================
# §9.4 寄存器组
# ==========================================================================


def enc_reg_read(addr: int, length: int) -> bytes:
    _check(1 <= length <= C.REG_MAX_LEN, "REG_READ len 必须在 1..64")
    return bytes([addr & 0xFF, length])


def dec_reg_read_req(p: bytes) -> Tuple[int, int]:
    r = _R(p)
    return r.u8(), r.u8()


def enc_reg_write(addr: int, data: bytes) -> bytes:
    _check(1 <= len(data) <= C.REG_MAX_LEN, "REG_WRITE 数据长度必须在 1..64")
    return bytes([addr & 0xFF]) + bytes(data)


def dec_reg_write_req(p: bytes) -> Tuple[int, bytes]:
    r = _R(p)
    addr = r.u8()
    return addr, r.rest()


def enc_reg_update(addr: int, mask: int, value: int) -> bytes:
    return bytes([addr & 0xFF, mask & 0xFF, value & 0xFF])


@dataclass
class RegUpdateResp:
    old: int = 0
    new: int = 0

    LEN = 2

    @classmethod
    def decode(cls, p: bytes) -> "RegUpdateResp":
        r = _R(p)
        return cls(r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.old, self.new])


def enc_reg_read_multi(items: Sequence[Tuple[int, int]]) -> bytes:
    _check(1 <= len(items) <= 16, "REG_READ_MULTI n 必须在 1..16")
    total = sum(n for _a, n in items)
    _check(total <= 128, "REG_READ_MULTI Σlen 必须 ≤128")
    out = bytearray([len(items)])
    for addr, n in items:
        _check(1 <= n <= C.REG_MAX_LEN, "REG_READ_MULTI 单项 len 必须 1..64")
        out += bytes([addr & 0xFF, n])
    return bytes(out)


def dec_reg_read_multi_req(p: bytes) -> List[Tuple[int, int]]:
    r = _R(p)
    n = r.u8()
    _check(1 <= n <= 16, "REG_READ_MULTI n 越界")
    items = []
    for _ in range(n):
        items.append((r.u8(), r.u8()))
    return items


def enc_reg_dump(start: int, length: int) -> bytes:
    _check(1 <= length <= C.REG_MAX_LEN, "REG_DUMP len 必须在 1..64")
    return bytes([start & 0xFF, length])


@dataclass
class RegDumpResp:
    start: int = 0
    data: bytes = b""

    @classmethod
    def decode(cls, p: bytes) -> "RegDumpResp":
        r = _R(p)
        start = r.u8()
        return cls(start, r.rest())

    def encode(self) -> bytes:
        return bytes([self.start]) + bytes(self.data)


def enc_stream_write(addr: int, offset: int, data: bytes) -> bytes:
    _check(1 <= len(data) <= C.REG_MAX_LEN, "STREAM_WRITE len 必须在 1..64")
    return bytes([addr & 0xFF]) + u16(offset) + bytes([len(data)]) + bytes(data)


# ==========================================================================
# §9.5 器件组
# ==========================================================================


@dataclass
class AwState:
    """0x34 AW_GET_STATE 应答（5 字节）"""

    glb_state: int = 0
    sysst: int = 0
    sysst2: int = 0
    play_mode: int = C.MODE_UNKNOWN
    ok: int = 1

    LEN = 5

    @classmethod
    def decode(cls, p: bytes) -> "AwState":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.glb_state, self.sysst, self.sysst2, self.play_mode, self.ok])


@dataclass
class InitDefaultResp:
    steps_ok: int = 0
    steps_total: int = 0

    LEN = 2

    @classmethod
    def decode(cls, p: bytes) -> "InitDefaultResp":
        r = _R(p)
        return cls(r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.steps_ok, self.steps_total])


# ==========================================================================
# §9.6 播放组
# ==========================================================================


def enc_play(timeout_ms: int, override: int = 0) -> bytes:
    return u16(timeout_ms) + u8(override)


@dataclass
class PlayResp:
    actual_timeout_ms: int = 0

    LEN = 2

    @classmethod
    def decode(cls, p: bytes) -> "PlayResp":
        r = _R(p)
        return cls(r.u16())

    def encode(self) -> bytes:
        return u16(self.actual_timeout_ms)


def enc_play_multi(chan_mask: int, timeout_ms: int, skew_us: int, override: int) -> bytes:
    return bytes([chan_mask & 0xFF]) + u16(timeout_ms) + u16(skew_us) + u8(override)


@dataclass
class PlayMultiResp:
    started_mask: int = 0
    actual_skew_us: int = 0

    LEN = 3

    @classmethod
    def decode(cls, p: bytes) -> "PlayMultiResp":
        r = _R(p)
        return cls(r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.started_mask]) + u16(self.actual_skew_us)


def enc_trig_pulse(chan_mask: int, width_us: int, count: int,
                   period_ms: int, initial_delay_us: int) -> bytes:
    _check(width_us >= 1, "TRIG_PULSE width_us 必须 ≥1")
    _check(1 <= count <= 32, "TRIG_PULSE count 必须在 1..32")
    return bytes([chan_mask & 0xFF]) + u16(width_us) + u8(count) + \
        u16(period_ms) + u16(initial_delay_us)


def enc_trig_level(chan_mask: int, level: int, hold_ms: int) -> bytes:
    _check(hold_ms >= 1, "TRIG_LEVEL hold_ms 必须 ≥1（手册要求 ≥0.8 ms）")
    return bytes([chan_mask & 0xFF, 1 if level else 0]) + u16(hold_ms)


def enc_trig_pattern(chan_mask: int, on_us: int, off_us: int,
                     count: int, period_ms: int) -> bytes:
    return bytes([chan_mask & 0xFF]) + u16(on_us) + u16(off_us) + u16(count) + u16(period_ms)


@dataclass
class StartedMaskResp:
    started_mask: int = 0

    LEN = 1

    @classmethod
    def decode(cls, p: bytes) -> "StartedMaskResp":
        r = _R(p)
        return cls(r.u8())

    def encode(self) -> bytes:
        return bytes([self.started_mask])


@dataclass
class PlayState:
    """0x47 GET_PLAY_STATE 应答（4 字节）"""

    playing: int = 0
    glb_state: int = 0
    elapsed_ms: int = 0

    LEN = 4

    @classmethod
    def decode(cls, p: bytes) -> "PlayState":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.playing, self.glb_state]) + u16(self.elapsed_ms)

    @property
    def state_name(self) -> str:
        return C.glb_state_name(self.glb_state)


@dataclass
class StopAllResp:
    stopped_mask: int = 0

    LEN = 1

    @classmethod
    def decode(cls, p: bytes) -> "StopAllResp":
        r = _R(p)
        return cls(r.u8())

    def encode(self) -> bytes:
        return bytes([self.stopped_mask])


# ==========================================================================
# §9.7 参数组
# ==========================================================================


@dataclass
class SetProfileResp:
    result: int = 0
    first_bad_field: int = 0

    LEN = 2

    @classmethod
    def decode(cls, p: bytes) -> "SetProfileResp":
        r = _R(p)
        return cls(r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.result, self.first_bad_field])


def enc_cont_live(drv1_lvl: int, drv2_lvl: int) -> bytes:
    _check(0 <= drv1_lvl <= 127 and 0 <= drv2_lvl <= 127, "DRV*_LVL 必须在 0..127")
    return bytes([drv1_lvl, drv2_lvl])


def enc_set_gain(gain: int) -> bytes:
    return bytes([gain & 0xFF])


def enc_set_vbat(vbat_mode: int, vbat_ref: int) -> bytes:
    return bytes([vbat_mode & 0xFF, vbat_ref & 0xFF])


@dataclass
class RamParams:
    """0x55 SET_RAM_PARAMS（14 字节）"""

    wavseq: List[int] = field(default_factory=lambda: [0] * 8)
    mainloop: int = 0
    waitslot: int = 0
    seq_loop: List[int] = field(default_factory=lambda: [0] * 4)

    LEN = 14

    def encode(self) -> bytes:
        _check(len(self.wavseq) == 8, "wavseq 必须 8 项")
        _check(len(self.seq_loop) == 4, "seq_loop 必须 4 项")
        return bytes([v & 0xFF for v in self.wavseq] + [self.mainloop & 0xFF,
                                                        self.waitslot & 0xFF]
                     + [v & 0xFF for v in self.seq_loop])

    @classmethod
    def decode(cls, p: bytes) -> "RamParams":
        r = _R(p)
        wavseq = [r.u8() for _ in range(8)]
        mainloop = r.u8()
        waitslot = r.u8()
        seq_loop = [r.u8() for _ in range(4)]
        return cls(wavseq, mainloop, waitslot, seq_loop)


@dataclass
class ContParams:
    """0x56 SET_CONT_PARAMS（14 字节）"""

    en_f0_det: int = 0
    sin_mode: int = 0
    edge_fre: int = 0
    f_pre: int = 141
    drv_width: int = 106
    wait_num: int = 6
    brk_gain: int = 8
    track_en: int = 1
    drv1_lvl: int = 127
    drv2_lvl: int = 80
    drv1_time: int = 4
    drv2_time: int = 6
    brk_time: int = 8
    track_margin: int = 0x11

    LEN = 14

    def encode(self) -> bytes:
        _check(1 <= self.f_pre <= 255, "F_PRE 必须在 1..255")
        return bytes([
            self.en_f0_det & 1, self.sin_mode & 1, self.edge_fre & 0x0F,
            self.f_pre & 0xFF, self.drv_width & 0xFF, self.wait_num & 0xFF,
            self.brk_gain & 0x0F, self.track_en & 1, self.drv1_lvl & 0x7F,
            self.drv2_lvl & 0x7F, self.drv1_time & 0xFF, self.drv2_time & 0xFF,
            self.brk_time & 0xFF, self.track_margin & 0xFF,
        ])

    @classmethod
    def decode(cls, p: bytes) -> "ContParams":
        r = _R(p)
        vals = [r.u8() for _ in range(14)]
        return cls(*vals)

    @property
    def f0_hz(self) -> float:
        return 24000.0 / self.f_pre if self.f_pre else 0.0


@dataclass
class RtpParams:
    """0x57 SET_RTP_PARAMS（8 字节）"""

    wavdat_mode: int = 2
    auto_dynamic_sin: int = 0
    base_addr: int = 0x0800
    fifo_ae: int = 512
    fifo_af: int = 1536

    LEN = 8

    def encode(self) -> bytes:
        return bytes([self.wavdat_mode & 0xFF, self.auto_dynamic_sin & 1]) + \
            u16(self.base_addr) + u16(self.fifo_ae) + u16(self.fifo_af)

    @classmethod
    def decode(cls, p: bytes) -> "RtpParams":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u16(), r.u16(), r.u16())


def enc_rtp_data(offset: int, data: bytes) -> bytes:
    _check(1 <= len(data) <= C.REG_MAX_LEN, "RTP_DATA len 必须在 1..64")
    return u16(offset) + bytes([len(data)]) + bytes(data)


@dataclass
class TrigParams:
    """0x59 SET_TRIG_PARAMS（13 字节）"""

    trig1_pos_en: int = 1
    trig1_seq_p: int = 1
    trig1_neg_en: int = 0
    trig1_seq_n: int = 1
    trig1_polar: int = 0
    trig1_lev: int = 0
    trig1_brk: int = 1
    trig1_stop: int = 1
    trig1_prio: int = 3
    trg_gain: int = 0x80
    trig1_mode: int = 3
    intn_pin: int = 0
    start_dly: int = 8

    LEN = 13

    def encode(self) -> bytes:
        return bytes([
            self.trig1_pos_en & 1, self.trig1_seq_p & 0x7F,
            self.trig1_neg_en & 1, self.trig1_seq_n & 0x7F,
            self.trig1_polar & 1, self.trig1_lev & 1,
            self.trig1_brk & 1, self.trig1_stop & 1,
            self.trig1_prio & 0x03, self.trg_gain & 0xFF,
            self.trig1_mode & 0x07, self.intn_pin & 1,
            self.start_dly & 0xFF,
        ])

    @classmethod
    def decode(cls, p: bytes) -> "TrigParams":
        r = _R(p)
        vals = [r.u8() for _ in range(13)]
        return cls(*vals)

    # ---- 触发方式联动（数据手册 Table 4 真值表） ----

    TRIG_MODES = [
        "上升沿", "下降沿", "双边沿", "高电平", "低电平",
    ]

    def apply_trigger_mode(self, name: str) -> None:
        """按界面下拉框的 5 种组合，一次性设置 4 个位。"""
        if name == "上升沿":
            self.trig1_lev, self.trig1_polar = 0, 0
            self.trig1_pos_en, self.trig1_neg_en = 1, 0
        elif name == "下降沿":
            self.trig1_lev, self.trig1_polar = 0, 0
            self.trig1_pos_en, self.trig1_neg_en = 0, 1
        elif name == "双边沿":
            self.trig1_lev, self.trig1_polar = 0, 0
            self.trig1_pos_en, self.trig1_neg_en = 1, 1
        elif name == "高电平":
            self.trig1_lev, self.trig1_polar = 1, 0
        elif name == "低电平":
            self.trig1_lev, self.trig1_polar = 1, 1
        else:
            raise ValueError(f"未知触发方式：{name}")

    @property
    def trigger_mode(self) -> str:
        if self.trig1_lev:
            return "低电平" if self.trig1_polar else "高电平"
        if self.trig1_pos_en and self.trig1_neg_en:
            return "双边沿"
        if self.trig1_pos_en:
            return "上升沿"
        if self.trig1_neg_en:
            return "下降沿"
        return "无（未使能任何边沿）"


# ==========================================================================
# §9.8 波形组
# ==========================================================================


def enc_wave_begin(sample_rate_code: int, wave_count: int, flags: int = 0) -> bytes:
    _check(1 <= wave_count <= C.WAVE_MAX_WAVES, "wave_count 必须在 1..127")
    return bytes([sample_rate_code & 0xFF, wave_count & 0xFF, flags & 0xFF])


@dataclass
class WaveBeginResp:
    session_id: int = 0
    max_total_samples: int = 0
    sram_free: int = 0

    LEN = 5

    @classmethod
    def decode(cls, p: bytes) -> "WaveBeginResp":
        r = _R(p)
        return cls(r.u8(), r.u16(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.session_id]) + u16(self.max_total_samples) + u16(self.sram_free)


def enc_wave_add(wave_id: int, offset: int, total_samples: int,
                 samples: bytes, last: bool = False) -> bytes:
    _check(1 <= wave_id <= C.WAVE_MAX_WAVES, "wave_id 必须在 1..127")
    _check(1 <= len(samples) <= C.WAVE_MAX_CHUNK_SAMPLES,
           f"WAVE_ADD 单次样本数必须在 1..{C.WAVE_MAX_CHUNK_SAMPLES}")
    flags = 0x01 if last else 0x00
    return bytes([wave_id & 0xFF]) + u16(offset) + u16(total_samples) + \
        bytes([flags]) + bytes(samples)


@dataclass
class WaveAddResp:
    wave_id: int = 0
    offset_ack: int = 0

    LEN = 3

    @classmethod
    def decode(cls, p: bytes) -> "WaveAddResp":
        r = _R(p)
        return cls(r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.wave_id]) + u16(self.offset_ack)


@dataclass
class WaveLibEntry:
    wave_id: int = 0
    start: int = 0
    end: int = 0

    @property
    def length(self) -> int:
        return self.end - self.start + 1 if self.end >= self.start else 0

    def encode(self) -> bytes:
        return bytes([self.wave_id]) + u16(self.start) + u16(self.end)

    @staticmethod
    def decode(r: "_R") -> "WaveLibEntry":
        return WaveLibEntry(r.u8(), r.u16(), r.u16())


@dataclass
class WaveCommitResp:
    result: int = 0
    total_bytes: int = 0
    lib_crc16: int = 0
    base_addr: int = 0
    entries: List[WaveLibEntry] = field(default_factory=list)

    @property
    def wave_count(self) -> int:
        return len(self.entries)

    @classmethod
    def decode(cls, p: bytes) -> "WaveCommitResp":
        r = _R(p)
        result = r.u8()
        total = r.u16()
        crc = r.u16()
        n = r.u8()
        base = r.u16()
        entries = [WaveLibEntry.decode(r) for _ in range(n)]
        return cls(result, total, crc, base, entries)

    def encode(self) -> bytes:
        out = bytearray([self.result]) + u16(self.total_bytes) + u16(self.lib_crc16) + \
            bytes([len(self.entries)]) + u16(self.base_addr)
        for e in self.entries:
            out += e.encode()
        return bytes(out)


@dataclass
class WaveLibrary:
    lib_version: int = 0
    total_bytes: int = 0
    base_addr: int = 0
    entries: List[WaveLibEntry] = field(default_factory=list)

    @property
    def wave_count(self) -> int:
        return len(self.entries)

    @classmethod
    def decode(cls, p: bytes) -> "WaveLibrary":
        r = _R(p)
        ver = r.u8()
        n = r.u8()
        total = r.u16()
        base = r.u16()
        entries = [WaveLibEntry.decode(r) for _ in range(n)]
        return cls(ver, total, base, entries)

    def encode(self) -> bytes:
        out = bytearray([self.lib_version, len(self.entries)]) + \
            u16(self.total_bytes) + u16(self.base_addr)
        for e in self.entries:
            out += e.encode()
        return bytes(out)


def enc_wave_readback(wave_id: int, offset: int, length: int) -> bytes:
    _check(1 <= length <= C.REG_MAX_LEN, "WAVE_READBACK len 必须在 1..64")
    return bytes([wave_id & 0xFF]) + u16(offset) + bytes([length])


@dataclass
class WaveReadbackResp:
    wave_id: int = 0
    offset: int = 0
    samples: bytes = b""

    @classmethod
    def decode(cls, p: bytes) -> "WaveReadbackResp":
        r = _R(p)
        return cls(r.u8(), r.u16(), r.rest())

    def encode(self) -> bytes:
        return bytes([self.wave_id]) + u16(self.offset) + bytes(self.samples)

    @property
    def signed(self) -> List[int]:
        return [b - 256 if b > 127 else b for b in self.samples]


def enc_wave_queue(ids: Sequence[int]) -> bytes:
    _check(1 <= len(ids) <= 8, "WAVE_QUEUE ids 必须在 1..8")
    out = bytearray([len(ids)])
    for i in ids:
        out.append(i & 0xFF)
    return bytes(out)


def enc_wave_gen(wave_id: int, shape: int, freq_hz: int, amplitude: int,
                 cycles: int, sample_rate_code: int) -> bytes:
    return bytes([wave_id & 0xFF, shape & 0xFF]) + u16(freq_hz) + \
        bytes([amplitude & 0x7F]) + u16(cycles) + bytes([sample_rate_code & 0xFF])


@dataclass
class WaveGenResp:
    wave_id: int = 0
    samples_generated: int = 0

    LEN = 3

    @classmethod
    def decode(cls, p: bytes) -> "WaveGenResp":
        r = _R(p)
        return cls(r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.wave_id]) + u16(self.samples_generated)


# ==========================================================================
# §9.9 测量组
# ==========================================================================


def enc_f0_start(timeout_ms: int, mode: int) -> bytes:
    return u16(timeout_ms) + bytes([mode & 0xFF])


@dataclass
class F0Result:
    valid: int = 0
    mode: int = 0
    raw_h: int = 0
    raw_l: int = 0
    f0_hz_x10: int = 0xFFFF

    LEN = 6

    @classmethod
    def decode(cls, p: bytes) -> "F0Result":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.valid, self.mode, self.raw_h, self.raw_l]) + u16(self.f0_hz_x10)

    @property
    def raw(self) -> int:
        return (self.raw_h << 8) | self.raw_l

    @property
    def f0_hz(self) -> Optional[float]:
        """F0 = 192000 / (H*256+L)（一次性检测，实测修正基准，见 C.F0_RAW_BASE_ONESHOT）。
        raw 为 0 时返回 None（除零，必为无效）。"""
        if self.raw == 0:
            return None
        return C.F0_RAW_BASE_ONESHOT / self.raw

    def diagnosis(self) -> str:
        """valid=0 时给出的诊断提示（对应硬件问题 C-1）。"""
        if self.valid:
            f = self.f0_hz
            if f is None or not (50.0 <= f <= 500.0):
                return ("检测值可疑：F0 落在 50~500 Hz 之外。请检查马达是否接好、"
                        "HDP/HDN 是否受干扰（手册明确警告干扰会导致 F0 不准）。")
            return ""
        return ("F0 检测失败。最可能的原因（按概率排序）："
                "① 马达回路的串联电容未改成 0 Ω（硬件问题 C-1，会把马达与反馈回路隔断）；"
                "② 马达未接好或接错通道；③ 检测超时。"
                "建议先用 DET_READ(seq=3) 读 LRA 阻抗 RL 佐证，再检查接线。")


def enc_det_read(seq: int) -> bytes:
    return bytes([seq & 0xFF])


@dataclass
class DetReadResp:
    adc_data: int = 0
    avg_data: int = 0
    value_x100: int = 0xFFFF
    valid: int = 0
    seq_echo: int = 0

    LEN = 8

    @classmethod
    def decode(cls, p: bytes) -> "DetReadResp":
        r = _R(p)
        return cls(r.u16(), r.u16(), r.u16(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return u16(self.adc_data) + u16(self.avg_data) + u16(self.value_x100) + \
            bytes([self.valid, self.seq_echo])

    # ---- 换算（协议 §9.9 表格） ----

    @staticmethod
    def vbat_v(avg_data: int) -> float:
        """VDD = 6.1 × AVG_DATA / 1023"""
        return 6.1 * avg_data / 1023.0

    @staticmethod
    def rl_ohm(avg_data: int, d2s_gain: int = C.DET_RL_D2S_GAIN) -> float:
        """RL = 610 × AVG_DATA / (1023 × D2S_GAIN)

        ⚠️ D2S_GAIN 是"固件检测时实际写入 0x50 的倍率"，不是寄存器默认值 ×8。
        固件按手册要求为 RL 检测写入 ×20，用默认 ×8 现算会偏大 2.5 倍。
        """
        if d2s_gain <= 0:
            return float("nan")
        return 610.0 * avg_data / (1023.0 * d2s_gain)

    def value(self, d2s_gain: int = C.DET_RL_D2S_GAIN) -> Optional[float]:
        """按 seq 解出物理量：seq=0 → V，seq=3 → Ω，seq=4 → 原始值。

        直接返回**固件算好的** ``value_x100``（固件用检测时真正的 D2S_GAIN），
        所以这里的 ``d2s_gain`` 参数只是为兼容旧签名，不参与换算。
        """
        if not self.valid or self.value_x100 == 0xFFFF:
            return None
        return self.value_x100 / 100.0

    @property
    def value_unit(self) -> str:
        return {C.DET_SEQ_VBAT: "V", C.DET_SEQ_RL: "Ω", C.DET_SEQ_OS: ""}.get(
            self.seq_echo, "")


def enc_mon_start(chan_mask: int, period_ms: int, fields: int) -> bytes:
    _check(period_ms >= 2, "MON_START period_ms 下限 2 ms")
    return bytes([chan_mask & 0xFF]) + u16(period_ms) + u16(fields)


def enc_mon_stop(chan_mask: int) -> bytes:
    return bytes([chan_mask & 0xFF])


@dataclass
class Snapshot:
    """0x75 SNAPSHOT 应答（15 字节，全部为原始寄存器值）"""

    present: int = 0
    chipid_h: int = 0
    chipid_l: int = 0
    mode: int = C.MODE_UNKNOWN
    playing: int = 0
    glb_state: int = 0
    sysst: int = 0
    sysst2: int = 0
    reg24: int = 0      # F_LRA_F0_H
    reg25: int = 0      # F_LRA_F0_L
    reg26: int = 0      # CONT_F0_H
    reg27: int = 0      # CONT_F0_L
    reg51: int = 0      # DETRD1
    reg52: int = 0      # DETRD2
    reg53: int = 0      # DETRD3

    LEN = 15

    @classmethod
    def decode(cls, p: bytes) -> "Snapshot":
        r = _R(p)
        return cls(*[r.u8() for _ in range(15)])

    def encode(self) -> bytes:
        return bytes([self.present, self.chipid_h, self.chipid_l, self.mode,
                      self.playing, self.glb_state, self.sysst, self.sysst2,
                      self.reg24, self.reg25, self.reg26, self.reg27,
                      self.reg51, self.reg52, self.reg53])

    @property
    def glb_state_name(self) -> str:
        return C.glb_state_name(self.glb_state)

    @property
    def is_playing(self) -> bool:
        return bool(self.playing) or self.glb_state in C.GLB_STATE_PLAYING

    @property
    def f0_hz(self) -> Optional[float]:
        """一次性检测结果（0x24/0x25），基准 192000（实测修正）。"""
        raw = (self.reg24 << 8) | self.reg25
        return C.F0_RAW_BASE_ONESHOT / raw if raw else None

    @property
    def cont_f0_hz(self) -> Optional[float]:
        """连续跟踪结果（0x26/0x27），基准 384000（与手册一致）。"""
        raw = (self.reg26 << 8) | self.reg27
        return C.F0_RAW_BASE_CONT / raw if raw else None

    @property
    def avg_data(self) -> int:
        """AVG_DATA = (0x51 bit1:0) × 256 + 0x52"""
        return (self.reg51 & 0x03) * 256 + self.reg52

    @property
    def adc_data(self) -> int:
        """ADC_DATA = (0x51 bit3:2) × 256 + 0x53"""
        return ((self.reg51 >> 2) & 0x03) * 256 + self.reg53

    def vbat_v(self) -> float:
        return DetReadResp.vbat_v(self.avg_data)

    def rl_ohm(self, d2s_gain: int = 8) -> float:
        return DetReadResp.rl_ohm(self.avg_data, d2s_gain)


# ==========================================================================
# §9.10 测试组
# ==========================================================================


def enc_sweep_amp(mode: int, from_lvl: int, to_lvl: int, step: int,
                  dwell_ms: int, settle_ms: int) -> bytes:
    _check(step != 0, "SWEEP_AMP step 不可为 0")
    return bytes([mode & 0xFF, from_lvl & 0xFF, to_lvl & 0xFF, step & 0xFF]) + \
        u16(dwell_ms) + u16(settle_ms)


def enc_sweep_freq(from_hz: int, to_hz: int, step_hz: int, dwell_ms: int) -> bytes:
    _check(step_hz != 0, "SWEEP_FREQ step_hz 不可为 0")
    return u16(from_hz) + u16(to_hz) + u16(step_hz) + u16(dwell_ms)


@dataclass
class SweepResp:
    accepted: int = 0
    total_steps: int = 0

    LEN = 2

    @classmethod
    def decode(cls, p: bytes) -> "SweepResp":
        r = _R(p)
        return cls(r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.accepted, self.total_steps])


def enc_test_run(test_id: int, params: bytes = b"") -> bytes:
    return u16(test_id) + bytes(params)


@dataclass
class AcceptedResp:
    accepted: int = 0

    LEN = 1

    @classmethod
    def decode(cls, p: bytes) -> "AcceptedResp":
        r = _R(p)
        return cls(r.u8())

    def encode(self) -> bytes:
        return bytes([self.accepted])


# ==========================================================================
# §10 事件
# ==========================================================================


@dataclass
class EvtLog:
    level: int = 0
    text: str = ""

    @classmethod
    def decode(cls, p: bytes) -> "EvtLog":
        r = _R(p)
        return cls(r.u8(), r.text())

    def encode(self) -> bytes:
        return bytes([self.level & 0xFF]) + self.text.encode("utf-8")


@dataclass
class EvtPlayStarted:
    mode: int = 0
    timeout_ms: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtPlayStarted":
        r = _R(p)
        return cls(r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.mode]) + u16(self.timeout_ms)


@dataclass
class EvtPlayDone:
    reason: int = 0
    elapsed_ms: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtPlayDone":
        r = _R(p)
        return cls(r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.reason]) + u16(self.elapsed_ms)

    @property
    def reason_name(self) -> str:
        return C.PLAY_DONE_REASON_NAMES.get(self.reason, f"原因{self.reason}")


@dataclass
class EvtMeasureResult:
    kind: int = 0
    valid: int = 0
    value: int = 0
    raw_h: int = 0
    raw_l: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtMeasureResult":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u32(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.kind, self.valid]) + u32(self.value) + \
            bytes([self.raw_h, self.raw_l])

    @property
    def is_f0(self) -> bool:
        return self.kind == 0

    @property
    def mon_fields(self) -> int:
        return (self.kind >> 4) & 0x0F if (self.kind & 0x0F) == 2 else 0

    @property
    def f0_hz(self) -> Optional[float]:
        """一次性检测结果（基准 192000，实测修正）。"""
        raw = (self.raw_h << 8) | self.raw_l
        return C.F0_RAW_BASE_ONESHOT / raw if raw else None


@dataclass
class EvtSweepProgress:
    step: int = 0
    total: int = 0
    param_value: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtSweepProgress":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u16())

    def encode(self) -> bytes:
        return bytes([self.step, self.total]) + u16(self.param_value)


@dataclass
class EvtSweepDone:
    steps: int = 0
    status: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtSweepDone":
        r = _R(p)
        return cls(r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.steps, self.status])


@dataclass
class EvtError:
    errcode: int = 0
    cmd: int = 0
    text: str = ""

    @classmethod
    def decode(cls, p: bytes) -> "EvtError":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.text())

    def encode(self) -> bytes:
        return bytes([self.errcode, self.cmd]) + self.text.encode("utf-8")

    @property
    def err_name(self) -> str:
        return C.err_name(self.errcode)


@dataclass
class EvtSafetyTrip:
    reason: int = 0
    affected_mask: int = 0
    detail: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtSafetyTrip":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8())

    def encode(self) -> bytes:
        return bytes([self.reason, self.affected_mask, self.detail])

    @property
    def reason_name(self) -> str:
        return C.TRIP_REASON_NAMES.get(self.reason, f"原因{self.reason}")


@dataclass
class EvtTestProgress:
    test_id: int = 0
    step: int = 0
    total: int = 0
    status: int = 0
    text: str = ""

    @classmethod
    def decode(cls, p: bytes) -> "EvtTestProgress":
        r = _R(p)
        return cls(r.u16(), r.u8(), r.u8(), r.u8(), r.text())

    def encode(self) -> bytes:
        return u16(self.test_id) + bytes([self.step, self.total, self.status]) + \
            self.text.encode("utf-8")

    @property
    def status_name(self) -> str:
        return C.TEST_STATUS_NAMES.get(self.status, f"状态{self.status}")


@dataclass
class EvtSelftestResult:
    overall: int = 0
    present_mask: int = 0
    bus_sel: int = 0
    tca: int = 0
    vreg_mask: int = 0
    issue_count: int = 0
    text: str = ""

    @classmethod
    def decode(cls, p: bytes) -> "EvtSelftestResult":
        r = _R(p)
        return cls(r.u8(), r.u8(), r.u8(), r.u8(), r.u8(), r.u8(), r.text())

    def encode(self) -> bytes:
        return bytes([self.overall, self.present_mask, self.bus_sel, self.tca,
                      self.vreg_mask, self.issue_count]) + self.text.encode("utf-8")

    @property
    def overall_name(self) -> str:
        return {0: "通过", 1: "有问题", 2: "失败"}.get(self.overall, f"状态{self.overall}")

    @property
    def bus_name(self) -> str:
        return {1: "LP", 2: "HP"}.get(self.bus_sel, "未知")


@dataclass
class EvtBusRecovered:
    method: int = 0

    @classmethod
    def decode(cls, p: bytes) -> "EvtBusRecovered":
        r = _R(p)
        return cls(r.u8())

    def encode(self) -> bytes:
        return bytes([self.method])

    @property
    def method_name(self) -> str:
        return C.BUS_RECOVER_METHODS.get(self.method, "未知")


# ==========================================================================
# 事件解码分发
# ==========================================================================

EVENT_DECODERS = {
    C.EVT_LOG: EvtLog,
    C.EVT_PLAY_STARTED: EvtPlayStarted,
    C.EVT_PLAY_DONE: EvtPlayDone,
    C.EVT_MEASURE_RESULT: EvtMeasureResult,
    C.EVT_SWEEP_PROGRESS: EvtSweepProgress,
    C.EVT_SWEEP_DONE: EvtSweepDone,
    C.EVT_ERROR: EvtError,
    C.EVT_SAFETY_TRIP: EvtSafetyTrip,
    C.EVT_TEST_PROGRESS: EvtTestProgress,
    C.EVT_SELFTEST_RESULT: EvtSelftestResult,
    C.EVT_BUS_RECOVERED: EvtBusRecovered,
}


def decode_event(frame) -> Optional[object]:
    """把事件帧解成对应的 dataclass；未知事件返回 None（不抛异常）。"""
    cls = EVENT_DECODERS.get(frame.cmd)
    if cls is None:
        return None
    try:
        return cls.decode(frame.payload)
    except PayloadError:
        return None
