"""Query a Glovity glove/receiver identity from a mixed binary/text COM stream."""

from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="查询 Glovity 设备身份和 STA MAC")
    parser.add_argument("--port", required=True, help="例如 COM16")
    parser.add_argument("--baud", type=int, default=921600,
                        help="手套默认921600；接收器默认2000000")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("缺少 pyserial，请运行：python -m pip install pyserial")
        return 2

    marker = b"$GLOVITY,DEVICE,INFO"
    pending = bytearray()
    try:
        with serial.Serial(args.port, args.baud, timeout=0.1) as connection:
            connection.reset_input_buffer()
            connection.write(b"$GLOVITY,DEVICE,QUERY,1\r\n")
            connection.flush()
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                chunk = connection.read(max(connection.in_waiting, 1))
                if not chunk:
                    continue
                pending.extend(chunk)
                start = pending.find(marker)
                if start >= 0:
                    end = pending.find(b"\n", start)
                    if end >= 0:
                        reply = bytes(pending[start:end + 1]).decode("ascii", "replace").strip()
                        print(reply)
                        fields = reply.split(",")
                        if fields:
                            print("STA MAC:", fields[-1])
                        return 0
                if len(pending) > 65536:
                    del pending[:-4096]
    except Exception as exc:
        print(f"串口错误：{exc}")
        return 2

    print(f"未找到设备回复；期间收到 {len(pending)} 字节。")
    print("请确认端口、固件版本、设备已重启，并关闭其他占用串口的软件。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
