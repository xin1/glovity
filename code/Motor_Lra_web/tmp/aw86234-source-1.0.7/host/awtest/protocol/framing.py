# -*- coding: utf-8 -*-
"""协议帧层：SOF / CRC16-CCITT-FALSE / 组帧 / 流式解帧。

本模块是 ``03_串口协议规范_v1.0.md`` 的可执行契约，与固件侧的
``firmware/main/proto_frame.c`` **逐字节一致**。

移植自 ``reference/protocol_reference.py``（该参考实现已在本机跑通
``reference/protocol_test_vectors.json`` 的全部向量：CRC 自检、向量往返、
粘包、逐字节断包、垃圾字节、CRC 损坏恢复、200 字节边界）。

设计要点
--------
* 字节流传输，不依赖波特率 / MTU / 包边界 / 时序
* 定长 8 字节头 + 变长载荷 + CRC16，单帧最大 210 字节
* 解析器可从任意字节偏移恢复同步（逐字节寻找 SOF，失败前进一字节）
* 所有多字节整数（含 CRC）一律 **little-endian**
* 无第三方依赖，可单独 ``pytest``
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

SOF0 = 0xAA
SOF1 = 0x55
PROTO_VERSION = 0x01

HEADER_LEN = 8          # SOF0 SOF1 VER FLAGS SEQ CMD CHAN LEN
CRC_LEN = 2
MIN_FRAME_LEN = HEADER_LEN + CRC_LEN                        # 10
MAX_PAYLOAD_LEN = 200
MAX_FRAME_LEN = HEADER_LEN + MAX_PAYLOAD_LEN + CRC_LEN      # 210

# FLAGS 位定义（协议 §4）
FLAG_RESPONSE = 0x01    # 1 = 应答帧，0 = 请求帧
FLAG_EVENT = 0x02       # 1 = 设备主动上报的事件帧（SEQ 恒为 0）
FLAG_ERROR = 0x04       # 1 = 载荷是错误应答（仅应答帧可置位）
FLAG_MORE = 0x08        # v1.0 保留：设备不得置位，主机收到应记为协议违例
FLAG_NORESP = 0x10      # 仅请求帧有效：主机声明"不需要应答"

FLAGS_RESERVED_MASK = 0xE0   # bit5~7 保留，必须为 0

# CHAN 特殊值（协议 §5）
CHAN_ALL = 0xFF         # 广播 / 全部通道
CHAN_NONE = 0xFE        # 系统级命令，与通道无关

# 只允许单个 bit 或 0x00 的通道掩码命令用到的掩码
VALID_CHAN_MASK = 0xFF


# --------------------------------------------------------------------------
# CRC16-CCITT-FALSE  (poly=0x1021, init=0xFFFF, MSB-first, 无反射, 无最终异或)
# 标准自检值： "123456789" -> 0x29B1
# 注意：这不是 XMODEM（init=0x0000，自检值 0x31C3），弄错会导致全部帧 CRC 失败。
# --------------------------------------------------------------------------


def _build_crc_table() -> List[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


CRC_TABLE = _build_crc_table()

CRC_SELF_TEST_INPUT = b"123456789"
CRC_SELF_TEST_EXPECT = 0x29B1


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """增量式 CRC16-CCITT-FALSE，便于逐字节喂入（与 C 侧逻辑一致）。"""
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


# --------------------------------------------------------------------------
# 帧结构
# --------------------------------------------------------------------------


@dataclass
class Frame:
    """一帧。``to_bytes()`` 组帧，``Frame.parse()`` 解帧。"""

    flags: int = 0
    seq: int = 0
    cmd: int = 0
    chan: int = CHAN_NONE
    payload: bytes = b""

    # ---- 便捷判断 ----
    @property
    def is_response(self) -> bool:
        return bool(self.flags & FLAG_RESPONSE)

    @property
    def is_event(self) -> bool:
        return bool(self.flags & FLAG_EVENT)

    @property
    def is_error(self) -> bool:
        return bool(self.flags & FLAG_ERROR)

    @property
    def more(self) -> bool:
        return bool(self.flags & FLAG_MORE)

    @property
    def noresp(self) -> bool:
        return bool(self.flags & FLAG_NORESP)

    @property
    def is_request(self) -> bool:
        return not self.is_response and not self.is_event

    @property
    def errcode(self) -> Optional[int]:
        """错误应答的错误码；非错误应答返回 None。"""
        return self.payload[0] if (self.is_error and self.payload) else None

    @property
    def err_code_safe(self) -> int:
        """错误码，载荷为空时回落到 ERR_INTERNAL(0x13)，永不抛异常。"""
        return self.payload[0] if self.payload else 0x13

    @property
    def error_text(self) -> str:
        """错误应答里可选的说明文本（UTF-8，尽力解码）。"""
        if not self.is_error or len(self.payload) <= 1:
            return ""
        return self.payload[1:].decode("utf-8", errors="replace")

    def to_bytes(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD_LEN:
            raise ValueError(f"payload too long: {len(self.payload)} > {MAX_PAYLOAD_LEN}")
        head = struct.pack(
            "<BBBBBBBB", SOF0, SOF1, PROTO_VERSION, self.flags & 0xFF,
            self.seq & 0xFF, self.cmd & 0xFF, self.chan & 0xFF, len(self.payload),
        )
        body = head[2:] + self.payload              # CRC 覆盖 VER..载荷末字节
        crc = crc16_ccitt(body)
        return head + self.payload + struct.pack("<H", crc)

    def to_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.to_bytes())

    def hex_dump(self) -> str:
        """紧凑无空格 hex（便于与测试向量比对）。"""
        return self.to_bytes().hex().upper()

    @staticmethod
    def parse(raw: bytes) -> "Frame":
        if len(raw) < MIN_FRAME_LEN:
            raise ValueError("frame too short")
        if raw[0] != SOF0 or raw[1] != SOF1:
            raise ValueError("bad SOF")
        if raw[2] != PROTO_VERSION:
            raise ValueError(f"bad proto version {raw[2]}")
        flags, seq, cmd, chan, plen = raw[3], raw[4], raw[5], raw[6], raw[7]
        if plen > MAX_PAYLOAD_LEN:
            raise ValueError(f"payload too long: {plen}")
        if len(raw) != HEADER_LEN + plen + CRC_LEN:
            raise ValueError("length mismatch")
        want = struct.unpack("<H", raw[HEADER_LEN + plen:])[0]
        got = crc16_ccitt(raw[2:HEADER_LEN + plen])
        if want != got:
            raise ValueError(f"crc mismatch: want {want:04X} got {got:04X}")
        return Frame(flags=flags, seq=seq, cmd=cmd, chan=chan,
                     payload=raw[HEADER_LEN:HEADER_LEN + plen])

    # ---- 构造助手 ----
    @staticmethod
    def request(cmd: int, payload: bytes = b"", seq: int = 0,
                chan: int = CHAN_NONE, noresp: bool = False) -> "Frame":
        return Frame(flags=(FLAG_NORESP if noresp else 0), seq=seq,
                     cmd=cmd, chan=chan, payload=payload)

    @staticmethod
    def response(cmd: int, payload: bytes = b"", seq: int = 0,
                 chan: int = CHAN_NONE, error: bool = False) -> "Frame":
        return Frame(flags=FLAG_RESPONSE | (FLAG_ERROR if error else 0),
                     seq=seq, cmd=cmd, chan=chan, payload=payload)

    @staticmethod
    def event(cmd: int, payload: bytes = b"", chan: int = CHAN_NONE) -> "Frame":
        return Frame(flags=FLAG_EVENT, seq=0, cmd=cmd, chan=chan, payload=payload)


# --------------------------------------------------------------------------
# 流式解析器 —— 从任意字节偏移恢复同步
# --------------------------------------------------------------------------


class FrameParser:
    """把串口字节流切成帧。对粘包、断包、垃圾字节、CRC 损坏都健壮。

    行为严格按协议 §3.2：

    ==================== ================================================
    情况                 行为
    ==================== ================================================
    找不到 SOF           丢弃垃圾字节；若末字节是 0xAA 则保留
    VER != 0x01          丢弃 1 字节重新对齐（不丢整帧）
    LEN > 200            丢弃 1 字节重新对齐
    帧未收完             继续等待
    CRC 不匹配           crc_errors++，丢弃 1 字节重新对齐
    一次 feed 多帧       全部产出（粘包）
    逐字节喂入           与整包喂入产出完全相同的帧序列
    ==================== ================================================
    """

    def __init__(self, max_buffer: int = MAX_FRAME_LEN * 2) -> None:
        self._buf = bytearray()
        self.max_buffer = max_buffer
        self.crc_errors = 0
        self.resyncs = 0
        self.frames_ok = 0

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[Frame]:
        if data:
            self._buf.extend(data)
            if len(self._buf) > self.max_buffer:
                # 缓冲区溢出保护：只保留最后 max_buffer 字节
                del self._buf[:len(self._buf) - self.max_buffer]
                self.resyncs += 1

        out: List[Frame] = []
        while True:
            # 1) 对齐到 SOF
            idx = self._buf.find(bytes((SOF0, SOF1)))
            if idx < 0:
                # 没有 SOF：保留最后一个可能是 SOF0 的字节
                if self._buf and self._buf[-1] == SOF0:
                    del self._buf[:-1]
                else:
                    if self._buf:
                        self.resyncs += 1
                    self._buf.clear()
                break
            if idx > 0:
                del self._buf[:idx]
                self.resyncs += 1

            # 2) 头部是否到齐
            if len(self._buf) < HEADER_LEN:
                break

            # 3) 版本号不对 -> 丢弃一个字节重新对齐
            if self._buf[2] != PROTO_VERSION:
                del self._buf[0]
                self.resyncs += 1
                continue

            plen = self._buf[7]
            if plen > MAX_PAYLOAD_LEN:
                del self._buf[0]
                self.resyncs += 1
                continue

            total = HEADER_LEN + plen + CRC_LEN
            if len(self._buf) < total:
                break                       # 等待更多数据（断包）

            raw = bytes(self._buf[:total])
            try:
                out.append(Frame.parse(raw))
                self.frames_ok += 1
                del self._buf[:total]
            except ValueError:
                self.crc_errors += 1
                del self._buf[0]            # 丢一字节重新同步
                self.resyncs += 1
        return out


def frames_to_stream(frames: List[Frame]) -> bytes:
    """把若干帧拼成一个字节流（测试与粘包模拟用）。"""
    return b"".join(f.to_bytes() for f in frames)
