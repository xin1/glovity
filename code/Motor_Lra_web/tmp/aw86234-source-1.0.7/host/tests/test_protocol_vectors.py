# -*- coding: utf-8 -*-
"""协议一致性：11 条测试向量 + CRC 自检 + 解析器健壮性用例。

向量文件：``reference/protocol_test_vectors.json``（固件侧 C 单测使用**同一份**）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from awtest.protocol import framing as F
from awtest.protocol.framing import Frame, FrameParser, crc16_ccitt

HERE = os.path.dirname(os.path.abspath(__file__))
VECTOR_PATH = os.path.abspath(os.path.join(HERE, "..", "..", "reference",
                                           "protocol_test_vectors.json"))


@pytest.fixture(scope="module")
def vectors():
    with open(VECTOR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# CRC
# --------------------------------------------------------------------------


def test_crc_self_test():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_crc_is_not_xmodem():
    # XMODEM 自检值是 0x31C3；确认我们没有误用初值 0x0000
    assert crc16_ccitt(b"123456789", 0x0000) != 0x29B1


def test_crc_empty_is_init():
    assert crc16_ccitt(b"") == 0xFFFF


def test_crc_incremental_matches_oneshot():
    data = bytes(range(256)) * 2
    one = crc16_ccitt(data)
    inc = 0xFFFF
    for i in range(0, len(data), 7):
        inc = crc16_ccitt(data[i:i + 7], inc)
    assert inc == one


def test_frame_limits(vectors):
    assert F.MAX_FRAME_LEN == 210
    assert F.MAX_PAYLOAD_LEN == 200
    assert vectors["max_frame_len"] == 210
    assert vectors["max_payload_len"] == 200
    assert vectors["protocol_version"] == F.PROTO_VERSION


# --------------------------------------------------------------------------
# 11 条向量
# --------------------------------------------------------------------------


def test_all_vectors_present(vectors):
    assert len(vectors["vectors"]) == 11


@pytest.mark.parametrize("index", range(11))
def test_vector_roundtrip(vectors, index):
    v = vectors["vectors"][index]
    raw = bytes.fromhex(v["frame_hex"])

    # 长度与 CRC 期望值
    assert len(raw) == v["frame_len"], f"vector {index} 长度不符"
    assert int.from_bytes(raw[-2:], "little") == v["crc16"], f"vector {index} CRC 不符"

    # 解帧 -> 字段逐项一致
    f = Frame.parse(raw)
    assert f.flags == v["flags"]
    assert f.seq == v["seq"]
    assert f.cmd == v["cmd"]
    assert f.chan == v["chan"]
    assert f.payload.hex().upper() == v["payload_hex"]

    # 重新组帧 -> 字节完全一致
    assert f.to_bytes() == raw, f"vector {index} 组帧不一致"
    assert f.hex_dump() == v["frame_hex"].upper()


# --------------------------------------------------------------------------
# 解析器健壮性（协议 §3.2）
# --------------------------------------------------------------------------


def _raw(vectors, i):
    return bytes.fromhex(vectors["vectors"][i]["frame_hex"])


def test_parser_sticky_packets_and_garbage(vectors):
    stream = _raw(vectors, 0) + b"\x00\xff\xaa" + _raw(vectors, 1) + _raw(vectors, 2)
    p = FrameParser()
    frames = p.feed(stream)
    assert len(frames) == 3
    assert [f.cmd for f in frames] == [0x01, 0x01, 0x20]


def test_parser_byte_by_byte_equals_bulk(vectors):
    stream = b"".join(_raw(vectors, i) for i in (0, 1, 2, 4, 6, 10))
    bulk = FrameParser().feed(stream)
    p = FrameParser()
    fed = []
    for i in range(len(stream)):
        fed.extend(p.feed(stream[i:i + 1]))
    assert [f.to_bytes() for f in fed] == [f.to_bytes() for f in bulk]
    assert len(fed) == 6


def test_parser_recovers_from_crc_error(vectors):
    bad = bytearray(_raw(vectors, 1))
    bad[-1] ^= 0xFF
    p = FrameParser()
    got = p.feed(bytes(bad) + _raw(vectors, 4))
    assert len(got) == 1
    assert p.crc_errors >= 1
    assert got[0].cmd == 0x20


def test_parser_resync_on_bad_version(vectors):
    raw = bytearray(_raw(vectors, 2))
    raw[2] = 0x02                       # VER 错
    p = FrameParser()
    got = p.feed(bytes(raw) + _raw(vectors, 0))
    assert len(got) == 1
    assert got[0].cmd == 0x01


def test_parser_rejects_oversized_len(vectors):
    raw = bytearray(_raw(vectors, 2))
    raw[7] = 201                        # LEN > 200
    p = FrameParser()
    got = p.feed(bytes(raw) + _raw(vectors, 0))
    assert len(got) == 1
    assert got[0].cmd == 0x01


def test_parser_keeps_trailing_sof0():
    p = FrameParser()
    assert p.feed(b"\x00\x11\xaa") == []
    assert p.buffered == 1
    out = p.feed(b"\x55" + Frame.request(0x01, b"", seq=1).to_bytes()[2:])
    assert len(out) == 1


def test_parser_max_frame_boundary(vectors):
    raw = _raw(vectors, 9)
    assert len(raw) == 210
    p = FrameParser()
    got = p.feed(raw)
    assert len(got) == 1
    assert len(got[0].payload) == 200


def test_frame_parse_rejects_bad_soflen_crc(vectors):
    raw = _raw(vectors, 0)
    with pytest.raises(ValueError):
        Frame.parse(b"\xab\x55" + raw[2:])
    with pytest.raises(ValueError):
        Frame.parse(raw[:-1])


def test_payload_too_long_raises():
    with pytest.raises(ValueError):
        Frame(cmd=0x01, payload=b"\x00" * 201).to_bytes()


# --------------------------------------------------------------------------
# FLAGS 语义
# --------------------------------------------------------------------------


def test_flags_helpers():
    resp = Frame.response(0x20, b"\x23", seq=2, chan=0)
    assert resp.is_response and not resp.is_event and resp.is_request is False
    err = Frame.response(0x20, b"\x05CH7 no ACK", seq=7, chan=7, error=True)
    assert err.is_error and err.errcode == 0x05
    assert "CH7 no ACK" in err.error_text
    evt = Frame.event(0xE2, b"\x00\xe0\x01", chan=2)
    assert evt.is_event and evt.seq == 0
    req = Frame.request(0x01, b"", seq=3, noresp=True)
    assert req.noresp and req.is_request


def test_vector6_more_bit_is_a_violation_for_v1(vectors):
    """v1.0 设备不得置位 MORE；上位机收到必须能识别出来。"""
    f = Frame.parse(_raw(vectors, 6))
    assert f.more is True
