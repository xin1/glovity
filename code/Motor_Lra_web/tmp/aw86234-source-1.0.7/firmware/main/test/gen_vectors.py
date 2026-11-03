#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 ``reference/protocol_test_vectors.json`` 生成 C 头文件。

**两边共用同一组测试向量**（验收标准第 11 条）：
固件侧不手抄向量，而是由本脚本从 JSON 生成 ``vectors_generated.h``，
避免"两边各写一份、悄悄漂移"。

用法：
    python firmware/main/test/gen_vectors.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
JSON_PATH = os.path.join(ROOT, "reference", "protocol_test_vectors.json")
OUT_PATH = os.path.join(HERE, "vectors_generated.h")


def main() -> int:
    with open(JSON_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    lines = [
        "/**",
        " * @file  vectors_generated.h",
        " * @brief 协议测试向量 —— **由 gen_vectors.py 自动生成，请勿手工编辑**。",
        " *",
        " * 来源：reference/protocol_test_vectors.json （与上位机 pytest 用的同一份文件）",
        f" * 协议版本：{data['protocol_version']}  向量条数：{len(data['vectors'])}",
        " */",
        "",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        f"#define VECTORS_PROTO_VERSION 0x{int(data['protocol_version']):02X}u",
        f"#define VECTORS_MAX_FRAME_LEN {int(data['max_frame_len'])}u",
        f"#define VECTORS_MAX_PAYLOAD_LEN {int(data['max_payload_len'])}u",
        f"#define VECTORS_CRC_SELFTEST_INPUT \"{data['crc']['self_test']['input']}\"",
        f"#define VECTORS_CRC_SELFTEST_EXPECT {data['crc']['self_test']['expect']}u",
        "",
        "typedef struct {",
        "    const char *desc;",
        "    const char *frame_hex;",
        "    uint16_t    frame_len;",
        "    uint16_t    crc16;",
        "} proto_test_vector_t;",
        "",
        "static const proto_test_vector_t k_proto_vectors[] = {",
    ]
    for v in data["vectors"]:
        desc = v["description"].replace('"', '\\"')
        lines.append('    { "%s",' % desc)
        lines.append('      "%s", %d, 0x%04Xu },' % (v["frame_hex"], v["frame_len"], v["crc16"]))
    lines.append("};")
    lines.append("")
    lines.append("#define VECTORS_COUNT (sizeof(k_proto_vectors) / sizeof(k_proto_vectors[0]))")
    lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    print(f"已生成 {OUT_PATH}（{len(data['vectors'])} 条向量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
