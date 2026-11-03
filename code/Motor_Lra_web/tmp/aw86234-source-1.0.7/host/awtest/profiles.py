# -*- coding: utf-8 -*-
"""参数 Profile（协议附录 B 的 64 字节布局）：dataclass + 范围校验 + JSON 存取。

**本文件是 Profile 字段范围与默认值的单一事实来源**：
上位机界面控件、脚本引擎、单测都从这里取；固件侧对应
``firmware/main/wave_codec.c`` 里的 ``profile_validate()``（同一份规则）。

不依赖任何 GUI 模块，可单独 pytest。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .protocol import commands as C

PROFILE_VER = 0x01
PROFILE_LEN = 64


def _pad(values: Sequence[int], n: int) -> List[int]:
    """把整型序列补齐/截断到 n 项（不足补 0）。"""
    out = [int(v) & 0xFF for v in list(values)[:n]]
    out.extend([0] * (n - len(out)))
    return out

# 字段名 -> (最小, 最大, 中文名, 寄存器说明)
FIELD_SPECS: Dict[str, Tuple[int, int, str, str]] = {
    "profile_ver": (1, 1, "Profile 版本", "必须 = 1"),
    "mode": (0, 3, "播放模式", "0x08 PLAY_MODE：0=RAM 1=RTP 2=CONT 3=TRIG"),
    "gain": (0, 255, "增益 GAIN", "0x07，倍率 = code/128，默认 128 = 1.00×"),
    "brk_en": (0, 1, "自动刹车", "0x08 bit2，默认开"),
    "stop_mode": (0, 1, "停止方式", "0x08 bit3：0=播完停 1=立即停"),
    "vbat_mode": (0, 1, "VBAT 模式", "0x4E：0=软件调节 1=硬件调节"),
    "vbat_ref": (0, 7, "VBAT 参考", "0x4F bit6:4：3.3/3.6/4.0/4.2/4.5/4.8/5.0/5.5 V"),
    "en_fir": (0, 1, "EN_FIR", "0x45 bit1，默认必须为 1（写 0x45 时要保住）"),
    "mainloop": (0, 15, "主循环", "0x16 bit3:0：0..14 播 (n+1) 次；15=无限循环（持续振动）"),
    "waitslot": (0, 3, "等待单位", "0x15 bit5:4：1/8/64/512 个采样周期"),
    "en_f0_det": (0, 1, "F0 检测使能", "0x17 bit5；=1 时自动刹车引擎不工作"),
    "sin_mode": (0, 1, "波形类型", "0x17 bit4：0=sin 1=cos"),
    "edge_fre": (0, 15, "边缘频率", "0x17 bit3:0；0..7=无边缘控制，8..15=码表"),
    "f_pre": (1, 255, "F_PRE", "0x18；F0 = 24000/F_PRE Hz"),
    "drv_width": (0, 255, "驱动宽度 DRV_WIDTH", "0x19；t = code/48000 秒，须小于 F0 半周期"),
    "wait_num": (0, 255, "循环衰减 WAIT_NUM",
                 "0x1A；t = code/48000 秒。★ CONT 下 >0 会在每个驱动段之间插停顿 → "
                 "“一块一块”；持续振动必须置 0"),
    "brk_gain": (0, 15, "刹车增益 BRK_GAIN", "0x1B bit3:0"),
    "track_en": (0, 1, "谐振跟踪 TRACK_EN", "0x1C bit7"),
    "drv1_lvl": (0, 127, "驱动电平 1 DRV1_LVL", "0x1C bit6:0；满幅阈值 >96"),
    "drv2_lvl": (0, 127, "驱动电平 2 DRV2_LVL", "0x1D bit6:0"),
    "drv1_time": (0, 255, "驱动时间 1", "0x1E；半周期数。持续振动取 255（254 ms@169Hz 量级）"),
    "drv2_time": (0, 255, "驱动时间 2", "0x1F；半周期数。持续振动取 0（否则插低幅度段）"),
    "brk_time": (0, 255, "刹车时间 BRK_TIME", "0x20；=0 时自动刹车失效。持续振动取 0"),
    "track_margin": (0, 255, "跟踪裕量 TRACK_MARGIN", "0x21；越小越准但环路越不稳"),
    "wavdat_mode": (0, 3, "RTP 采样率", "0x46 bit1:0；48K(1) 本板不可用"),
    "rtp_flags": (0, 3, "RTP 标志", "0x31：bit0=AUTO_DYNAMIC_SINE bit1=AUTO_SIN_96K"),
    "base_addr": (0, 0x0FFF, "Base Addr", "0x2C/0x2D，默认 0x0800"),
    "fifo_ae": (0, 4095, "FIFO 近空", "0x2E bit7:4 / 0x2F，默认 512"),
    "fifo_af": (0, 4095, "FIFO 近满", "0x2E bit3:0 / 0x30，默认 1536"),
    "trig1_pos_en": (0, 1, "正沿触发使能", "0x33 bit7；默认 0=不触发"),
    "trig1_seq_p": (0, 127, "正沿波形号", "0x33 bit6:0，默认 1"),
    "trig1_neg_en": (0, 1, "负沿触发使能", "0x36 bit7"),
    "trig1_seq_n": (0, 127, "负沿波形号", "0x36 bit6:0，默认 1"),
    "trig1_polar": (0, 1, "电平极性", "0x39 bit5：0=正电平有效 1=负电平有效"),
    "trig1_lev": (0, 1, "电平/边沿", "0x39 bit4：0=边沿 1=电平"),
    "trig1_brk": (1, 1, "TRIG 刹车使能", "0x39 bit3；TRIG 模式下必须 =1（不可关）"),
    "trig1_stop": (1, 1, "允许立即停止", "0x3A bit2；必须 =1（不可关，安全要求）"),
    "trig1_prio": (0, 3, "TRIG 优先级", "0x3E bit1:0，默认 3"),
    "trg_gain": (0, 255, "TRIG 增益", "0x43；倍率 = code/128，默认 0x80=1.00×。注意不是刹车位"),
    "trig1_mode": (3, 3, "TRG1_MODE", "0x45 bit6:4；必须 =3（b011）才是 TRIG"),
    "intn_pin": (0, 0, "INTN_PIN", "0x45 bit0；本板必须 =0（A1 作 TRIG1）"),
    "start_dly": (0, 255, "启动延时", "0x3C；t = code/48000 秒，默认 8≈166.7 µs"),
    "reserved1": (0, 0, "保留", "必须 0"),
}

# 枚举位域的中文码表
EDGE_FRE_TABLE = {0: "无边缘控制", 8: "200 Hz", 9: "210 Hz", 10: "260 Hz",
                  11: "280 Hz", 12: "300 Hz", 13: "600 Hz", 14: "700 Hz",
                  15: "800 Hz"}
for _i in range(1, 8):
    EDGE_FRE_TABLE[_i] = "无边缘控制"

VBAT_REF_TABLE = {0: 3.3, 1: 3.6, 2: 4.0, 3: 4.2, 4: 4.5, 5: 4.8, 6: 5.0, 7: 5.5}

RTP_RATE_TABLE = {0: "24 kHz", 1: "48 kHz（本板不可用）", 2: "12 kHz", 3: "12 kHz"}


@dataclass
class Profile:
    """协议附录 B 的 64 字节 Profile。

    字段顺序即为线上字节顺序，``to_bytes()`` 直接按此顺序打包（保留字节补 0）。
    """

    profile_ver: int = PROFILE_VER
    mode: int = C.MODE_RAM
    gain: int = 128
    brk_en: int = 1
    stop_mode: int = 0
    vbat_mode: int = 0
    vbat_ref: int = 2
    en_fir: int = 1

    # RAM 段
    wavseq: List[int] = field(default_factory=lambda: [0] * 8)
    mainloop: int = 0
    waitslot: int = 0
    seq_loop: List[int] = field(default_factory=lambda: [0] * 4)
    reserved_ram: List[int] = field(default_factory=lambda: [0] * 6)

    # CONT 段
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

    # RTP 段
    wavdat_mode: int = C.RATE_12K
    rtp_flags: int = 0
    base_addr: int = 0x0800
    fifo_ae: int = 512
    fifo_af: int = 1536

    # TRIG 段
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
    reserved1: int = 0

    # ---- 线序（附录 B 的偏移表，顺序即字节顺序） ----
    _BYTE_ORDER: Tuple[str, ...] = (
        "profile_ver", "mode", "gain", "brk_en", "stop_mode", "vbat_mode",
        "vbat_ref", "en_fir",
        "wavseq", "mainloop", "waitslot", "seq_loop", "reserved_ram",
        "en_f0_det", "sin_mode", "edge_fre", "f_pre", "drv_width", "wait_num",
        "brk_gain", "track_en", "drv1_lvl", "drv2_lvl", "drv1_time", "drv2_time",
        "brk_time", "track_margin",
        "wavdat_mode", "rtp_flags", "base_addr", "fifo_ae", "fifo_af",
        "trig1_pos_en", "trig1_seq_p", "trig1_neg_en", "trig1_seq_n",
        "trig1_polar", "trig1_lev", "trig1_brk", "trig1_stop", "trig1_prio",
        "trg_gain", "trig1_mode", "intn_pin", "start_dly", "reserved1",
    )

    # ---- 线序编解码 ----

    def to_bytes(self) -> bytes:
        """打包成协议要求的 64 字节（多字节字段小端）。"""
        out = bytearray()
        for key in ("profile_ver", "mode", "gain", "brk_en", "stop_mode",
                    "vbat_mode", "vbat_ref", "en_fir"):
            out.append(int(getattr(self, key)) & 0xFF)
        out += bytes(v & 0xFF for v in _pad(self.wavseq, 8))
        out.append(self.mainloop & 0xFF)
        out.append(self.waitslot & 0xFF)
        out += bytes(v & 0xFF for v in _pad(self.seq_loop, 4))
        out += bytes(v & 0xFF for v in _pad(self.reserved_ram, 6))
        for key in ("en_f0_det", "sin_mode", "edge_fre", "f_pre", "drv_width",
                    "wait_num", "brk_gain", "track_en", "drv1_lvl", "drv2_lvl",
                    "drv1_time", "drv2_time", "brk_time", "track_margin"):
            out.append(int(getattr(self, key)) & 0xFF)
        out.append(self.wavdat_mode & 0xFF)
        out.append(self.rtp_flags & 0xFF)
        out += int(self.base_addr).to_bytes(2, "little")
        out += int(self.fifo_ae).to_bytes(2, "little")
        out += int(self.fifo_af).to_bytes(2, "little")
        for key in ("trig1_pos_en", "trig1_seq_p", "trig1_neg_en", "trig1_seq_n",
                    "trig1_polar", "trig1_lev", "trig1_brk", "trig1_stop",
                    "trig1_prio", "trg_gain", "trig1_mode", "intn_pin",
                    "start_dly", "reserved1"):
            out.append(int(getattr(self, key)) & 0xFF)
        if len(out) != PROFILE_LEN:
            raise AssertionError(f"Profile 打包长度 {len(out)} != {PROFILE_LEN}")
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Profile":
        if len(data) < PROFILE_LEN:
            raise ValueError(f"Profile 载荷需要 {PROFILE_LEN} 字节，收到 {len(data)}")
        p = cls()
        i = 0

        def take(n: int) -> bytes:
            nonlocal i
            v = data[i:i + n]
            i += n
            return v

        p.profile_ver, p.mode, p.gain, p.brk_en, p.stop_mode, p.vbat_mode, \
            p.vbat_ref, p.en_fir = list(take(8))
        p.wavseq = list(take(8))
        p.mainloop, p.waitslot = list(take(2))
        p.seq_loop = list(take(4))
        p.reserved_ram = list(take(6))
        (p.en_f0_det, p.sin_mode, p.edge_fre, p.f_pre, p.drv_width, p.wait_num,
         p.brk_gain, p.track_en, p.drv1_lvl, p.drv2_lvl, p.drv1_time, p.drv2_time,
         p.brk_time, p.track_margin) = list(take(14))
        p.wavdat_mode, p.rtp_flags = list(take(2))
        p.base_addr = int.from_bytes(take(2), "little")
        p.fifo_ae = int.from_bytes(take(2), "little")
        p.fifo_af = int.from_bytes(take(2), "little")
        (p.trig1_pos_en, p.trig1_seq_p, p.trig1_neg_en, p.trig1_seq_n,
         p.trig1_polar, p.trig1_lev, p.trig1_brk, p.trig1_stop, p.trig1_prio,
         p.trg_gain, p.trig1_mode, p.intn_pin, p.start_dly,
         p.reserved1) = list(take(14))
        return p

    # ---- JSON ----

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Profile":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in known}
        obj = cls(**kwargs)
        for key in ("wavseq", "seq_loop", "reserved_ram"):
            v = getattr(obj, key)
            if not isinstance(v, list):
                raise ValueError(f"{key} 必须是数组")
        return obj

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @classmethod
    def load(cls, path: str) -> "Profile":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    # ---- 校验 ----

    def validate(self) -> List[Tuple[str, str]]:
        """返回**阻塞性**错误 [(字段名, 原因)]；空列表表示可以下发。

        规则与固件 ``aw_profile.c`` 的 ``aw_profile_validate()`` 必须一致。
        仅仅是"提醒"的信息（例如 `en_f0_det` 会让自动刹车失效）请用
        :meth:`warnings`，**不要**放在这里——否则会把合法 Profile 拦下来。
        """
        problems: List[Tuple[str, str]] = []
        if self.profile_ver != PROFILE_VER:
            problems.append(("profile_ver", f"必须为 {PROFILE_VER}，实际 {self.profile_ver}"))
        for name, (lo, hi, _cn, _note) in FIELD_SPECS.items():
            if name == "profile_ver":
                continue
            val = getattr(self, name)
            if not isinstance(val, int):
                problems.append((name, f"必须是整数，实际 {val!r}"))
                continue
            if not (lo <= val <= hi):
                problems.append((name, f"超出范围 {lo}..{hi}，实际 {val}"))
        if len(self.wavseq) != 8:
            problems.append(("wavseq", "必须 8 项"))
        if len(self.seq_loop) != 4:
            problems.append(("seq_loop", "必须 4 项"))
        if self.wavdat_mode == C.RATE_48K:
            problems.append(("wavdat_mode", "48 kHz 在本板不可用（需 ≥1 MHz I2C）"))
        if self.drv_width and self.f_pre:
            half_period_us = 1e6 / (24000.0 / self.f_pre) / 2.0
            width_us = self.drv_width / 48000.0 * 1e6
            if width_us >= half_period_us:
                problems.append(("drv_width",
                                 f"驱动宽度 {width_us:.1f} µs 不小于 F0 半周期 "
                                 f"{half_period_us:.1f} µs"))
        return problems

    def warnings(self) -> List[Tuple[str, str]]:
        """非阻塞提醒：下发仍然成功，但界面应当显示（黄色）。"""
        out: List[Tuple[str, str]] = []
        if self.brk_time == 0:
            out.append(("brk_time",
                        "=0 会让自动刹车引擎失效（持续振动配方本就需要 0，"
                        "但注意此时 STOP 后靠自然衰减停）"))
        if self.en_f0_det == 1:
            out.append(("en_f0_det", "=1 时自动刹车引擎不工作"))
        if self.trig1_lev == 0 and not self.trig1_pos_en and not self.trig1_neg_en:
            out.append(("trig1_pos_en", "边沿模式下正/负沿都没使能 → 给脉冲也不会触发"))
        # ★ 持续振动检查（2026-09 实机结论，见实测记录 §2.20d）
        if self.mode == C.MODE_CONT:
            if self.wait_num > 0:
                out.append(("wait_num",
                            "CONT 下 WAIT_NUM>0 会在每个驱动段之间插入停顿，"
                            "听感是“一块一块的”。要持续振动请置 0"))
            if self.drv1_time < 100:
                out.append(("drv1_time",
                            f"CONT 驱动段只有 {self.drv1_time} 个半周期（≈"
                            f"{self.drv1_time * 1e6 / (24000.0 / max(self.f_pre, 1)) / 2 / 1000:.0f} ms）；"
                            "持续振动建议 255"))
            if self.drv2_time or self.brk_time:
                out.append(("brk_time",
                            "CONT 稳态里的 DRV2 (0x1F) / 刹车 (0x20) 段会让振动发闷；"
                            "持续振动建议都置 0"))
        if self.mode == C.MODE_RAM and self.mainloop != 0x0F:
            out.append(("mainloop",
                        f"mainloop={self.mainloop} 表示只播 {self.mainloop + 1} 轮；"
                        "要循环播放请置 15（0xF）"))
        return out

    def is_valid(self) -> bool:
        return not self.validate()

    # ---- 便捷换算 ----

    @property
    def f0_hz(self) -> float:
        return 24000.0 / self.f_pre if self.f_pre else 0.0

    def set_f0_hz(self, hz: float) -> None:
        code = int(round(24000.0 / hz)) if hz > 0 else 255
        self.f_pre = max(1, min(255, code))

    @property
    def gain_x(self) -> float:
        return self.gain / 128.0

    @property
    def trg_gain_x(self) -> float:
        return self.trg_gain / 128.0

    def copy(self) -> "Profile":
        return Profile.from_bytes(self.to_bytes())


def diff(a: Profile, b: Profile) -> List[Tuple[str, Any, Any]]:
    """逐字段比对，返回 [(字段, a 的值, b 的值)]。用于"下发 → 回读 → 比对"。"""
    out = []
    for f in fields(Profile):
        if f.name.startswith("_"):
            continue
        va, vb = getattr(a, f.name), getattr(b, f.name)
        if va != vb:
            out.append((f.name, va, vb))
    return out


# --------------------------------------------------------------------------
# 预设 Profile（协议 §6 / 任务书要求开箱可用）
# --------------------------------------------------------------------------


def preset_ram_175hz() -> Profile:
    """RAM / 175 Hz 正弦 / 中等幅度 —— 首次验证用。"""
    p = Profile()
    p.mode = C.MODE_RAM
    p.gain = 128
    p.wavseq = [1, 0, 0, 0, 0, 0, 0, 0]
    p.mainloop = 0
    return p


def preset_cont_f0() -> Profile:
    """CONT / 170 Hz / DRV1=127 / 跟踪开 —— F0 检测用。"""
    p = Profile()
    p.mode = C.MODE_CONT
    p.set_f0_hz(170.0)
    p.drv1_lvl = 127
    p.drv2_lvl = 80
    p.track_en = 1
    p.en_f0_det = 1
    return p


def preset_trig_rising() -> Profile:
    """TRIG / 上升沿 / 正沿波形 1 —— 并发触发用。"""
    p = Profile()
    p.mode = C.MODE_TRIG
    p.trig1_lev, p.trig1_polar = 0, 0
    p.trig1_pos_en, p.trig1_neg_en = 1, 0
    p.trig1_seq_p = 1
    p.trig1_mode = 3
    p.intn_pin = 0
    p.trig1_brk = 1
    p.trig1_stop = 1
    return p


def preset_rtp_12k() -> Profile:
    """RTP 12K 流式测试。"""
    p = Profile()
    p.mode = C.MODE_RTP
    p.wavdat_mode = C.RATE_12K
    p.base_addr = 0x0800
    p.fifo_ae = 512
    p.fifo_af = 1536
    return p


PRESET_FACTORIES = {
    "RAM_175Hz_正弦": preset_ram_175hz,
    "CONT_170Hz_F0检测": preset_cont_f0,
    "TRIG_上升沿": preset_trig_rising,
    "RTP_12K_流式": preset_rtp_12k,
}


def write_preset_files(directory: str) -> List[str]:
    """把预设写入 ``resources/profiles/``，返回写出的文件名列表。"""
    os.makedirs(directory, exist_ok=True)
    out = []
    for name, factory in PRESET_FACTORIES.items():
        path = os.path.join(directory, f"{name}.json")
        factory().save(path)
        out.append(path)
    return out
