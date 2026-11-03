"""JY901P 11-IMU magnetic calibration through existing Glovity firmware."""

from __future__ import annotations

import argparse
import random
import time

from calibration_tools.common import TextLineExtractor, send_line, wait_for_line


def _direct_commands() -> tuple[str, str, str]:
    return "$GLOVITY,CAL,START", "$GLOVITY,CAL,STOP", "$GLOVITY,CAL,STATUS"


def _remote_commands(side: str, session: int, sequence: int) -> tuple[str, str, str]:
    prefix = "$GLOVITY,RX,CAL"
    return (
        f"{prefix},START,{side},{session},{sequence}",
        f"{prefix},STOP,{side},{session},{sequence}",
        f"{prefix},STATUS,{side},{session},{sequence}",
    )


def run(port_name: str, baud: int, side: str | None, duration: float) -> None:
    import serial

    if not 5 <= duration <= 50:
        raise ValueError("duration 必须在 5..50 秒；固件远程校准会在 60 秒超时")
    extractor = TextLineExtractor()
    session = random.randint(1, 0x7FFFFFFF)
    sequence = random.randint(1, 0xFFFF)

    with serial.Serial(port_name, baud, timeout=0.05, write_timeout=1.0) as port:
        port.reset_input_buffer()
        if side is None:
            start, stop, status = _direct_commands()
            started = lambda line: line == "$GLOVITY,CAL,STARTED"
            complete = lambda line: line == "$GLOVITY,CAL,COMPLETE"
        else:
            send_line(port, "$GLOVITY,RX,MAINT,ENTER")
            wait_for_line(port, extractor, [lambda x: x.startswith("$GLOVITY,RX,MAINT,READY")], 3)
            start, stop, status = _remote_commands(side, session, sequence)
            started = lambda line: ",CAL,STARTED," in line and f",{session},{sequence}" in line
            complete = lambda line: ",CAL,COMPLETE," in line and f",{session},{sequence}" in line

        try:
            send_line(port, status)
            time.sleep(0.2)
            send_line(port, start)
            line = wait_for_line(
                port, extractor,
                [started, lambda x: ",CAL,ERROR," in x or x.endswith(",NO_IMU")], 5,
            )
            if not started(line):
                raise RuntimeError(line)
            print("\n开始磁力计球面拟合校准：远离磁铁/电机/大块金属，缓慢转动手套，")
            print("让 X/Y/Z 三个轴分别覆盖完整一圈；手指保持自然展开。\n")
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                chunk = port.read(max(1, port.in_waiting))
                for line in extractor.feed(chunk):
                    if ",CAL,MAG," in line:
                        print(line)
                    elif ",CAL,ERROR," in line:
                        raise RuntimeError(line)
                remaining = max(0, int(deadline - time.monotonic()))
                print(f"\r剩余 {remaining:2d} 秒", end="", flush=True)
            print()
            send_line(port, stop)
            line = wait_for_line(
                port, extractor,
                [complete, lambda x: ",CAL,ERROR," in x], 8,
            )
            if not complete(line):
                raise RuntimeError(line)
            print("磁力计校准已写入全部在线 JY901P。")
        except BaseException:
            if side is not None:
                abort = f"$GLOVITY,RX,CAL,ABORT,{side},{session},{sequence}"
                send_line(port, abort)
            raise
        finally:
            if side is not None:
                send_line(port, "$GLOVITY,RX,MAINT,EXIT")


def main() -> None:
    parser = argparse.ArgumentParser(description="Glovity/JY901P 11路磁力计校准")
    parser.add_argument("--port", required=True, help="手套或接收器串口，如 COM5")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--side", choices=("left", "right"),
                        help="连接接收器做无线校准时指定；直连手套时不填")
    parser.add_argument("--duration", type=float, default=45.0, help="转动采集秒数，5..50")
    args = parser.parse_args()
    run(args.port, args.baud, args.side, args.duration)


if __name__ == "__main__":
    main()
