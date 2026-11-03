# -*- coding: utf-8 -*-
"""协议层：帧格式、命令码、payload 编解码、串口传输。

``framing.py`` 与固件 ``firmware/main/proto_frame.c`` 逐字节一致，
两者共同通过 ``reference/protocol_test_vectors.json`` 的全部 11 条向量。
"""

from .framing import (  # noqa: F401
    CHAN_ALL, CHAN_NONE, CRC_SELF_TEST_EXPECT, CRC_SELF_TEST_INPUT,
    FLAG_ERROR, FLAG_EVENT, FLAG_MORE, FLAG_NORESP, FLAG_RESPONSE,
    HEADER_LEN, MAX_FRAME_LEN, MAX_PAYLOAD_LEN, MIN_FRAME_LEN,
    PROTO_VERSION, SOF0, SOF1, Frame, FrameParser, crc16_ccitt,
    frames_to_stream,
)

__all__ = [
    "CHAN_ALL", "CHAN_NONE", "CRC_SELF_TEST_EXPECT", "CRC_SELF_TEST_INPUT",
    "FLAG_ERROR", "FLAG_EVENT", "FLAG_MORE", "FLAG_NORESP", "FLAG_RESPONSE",
    "HEADER_LEN", "MAX_FRAME_LEN", "MAX_PAYLOAD_LEN", "MIN_FRAME_LEN",
    "PROTO_VERSION", "SOF0", "SOF1", "Frame", "FrameParser", "crc16_ccitt",
    "frames_to_stream",
]
