"""Connect to a glove COM port and diagnose the incoming byte stream."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import dataclass
import re
import sys
import time
from pathlib import Path

from .protocol import AutoStreamDecoder, LegacyGloveFrame, MAGIC


_RX_STATS_PATTERN = re.compile(rb"\$GLOVITY,RX,STATS,(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\r?\n")


def _wait_for_receiver_reply(connection, marker: bytes, timeout: float, dump=None) -> bytes | None:
    """Read through binary frames until a complete receiver control reply arrives."""
    deadline = time.monotonic() + timeout
    pending = bytearray()
    while time.monotonic() < deadline:
        chunk = connection.read(max(connection.in_waiting, 1))
        if not chunk:
            continue
        if dump:
            dump.write(chunk)
        pending.extend(chunk)
        if marker == b"$GLOVITY,RX,STATS,":
            match = _RX_STATS_PATTERN.search(pending)
            if match:
                return match.group(0)
        else:
            start = pending.find(marker)
            if start >= 0:
                end = pending.find(b"\n", start)
                if end >= 0:
                    return bytes(pending[start : end + 1])
        if len(pending) > 4096:
            del pending[:-4096]
    return None


def _serial_modules():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError as exc:
        raise SystemExit(
            "缺少 pyserial。请在本目录运行：python -m pip install -r requirements.txt"
        ) from exc
    return serial, list_ports


def available_ports() -> list:
    _, list_ports = _serial_modules()
    return list(list_ports.comports())


def print_ports() -> int:
    ports = available_ports()
    if not ports:
        print("未发现串口。请确认手套已开机、USB数据线可用且驱动已安装。")
        return 1
    print("可用串口：")
    for port in ports:
        print(f"  {port.device:8} {port.description} [{port.hwid}]")
    return 0


@dataclass
class ProbeStats:
    started: float
    total_bytes: int = 0
    valid_frames: int = 0
    sequence_gaps: int = 0
    last_seq: int | None = None
    last_frame_at: float | None = None
    printable_bytes: int = 0
    magic_count: int = 0
    legacy_168_frames: int = 0
    legacy_170_frames: int = 0
    v2_frames: int = 0


def _choose_port(requested: str | None) -> str:
    if requested:
        return requested
    ports = available_ports()
    if len(ports) == 1:
        print(f"自动选择唯一串口：{ports[0].device} ({ports[0].description})")
        return ports[0].device
    if not ports:
        raise SystemExit("未发现串口。请连接手套后重试，或使用 --list。")
    print_ports()
    raise SystemExit("发现多个串口，请使用 --port COMx 指定手套端口。")


def _diagnosis(stats: ProbeStats, decoder: AutoStreamDecoder, preview: bytes, elapsed: float) -> str:
    if stats.valid_frames:
        return (
            f"成功：检测到{stats.valid_frames}个有效帧"
            f"（旧168={stats.legacy_168_frames}，旧170={stats.legacy_170_frames}，V2={stats.v2_frames}）；"
            f"平均帧率约{stats.valid_frames / max(elapsed, 1e-6):.2f} Hz。"
        )
    if stats.total_bytes == 0:
        return "未收到任何字节：检查端口、手套供电、USB数据线、OUTPUT_EN电平和固件USB输出模式。"
    printable_ratio = stats.printable_bytes / stats.total_bytes
    if printable_ratio > 0.80:
        text = preview.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        return f"收到的数据主要是文本，不是180字节二进制帧。文本预览：{text[:160]}"
    if stats.magic_count == 0:
        return (
            f"收到{stats.total_bytes}字节二进制数据，但没有AA 55帧头。"
            "可能是旧版协议、选错串口，或当前固件尚未实现V2协议。"
        )
    return (
            f"发现AA 55帧头但没有通过校验的新旧数据帧；"
        f"候选帧拒绝{decoder.rejected_candidates}次。可能是CRC、版本、长度或字段布局不一致。"
    )


def run_probe(args: argparse.Namespace) -> int:
    serial, _ = _serial_modules()
    port = _choose_port(args.port)
    decoder = AutoStreamDecoder(max_buffer=180 * 64)
    stats = ProbeStats(started=time.monotonic())
    preview = bytearray()
    recent_rates: deque[float] = deque(maxlen=100)
    dump = Path(args.save).open("wb") if args.save else None
    print(f"正在连接 {port}，baud={args.baud}。按 Ctrl+C 可提前停止。")
    try:
        with serial.Serial(port=port, baudrate=args.baud, timeout=0.2) as connection:
            connection.reset_input_buffer()
            if args.receiver_stats:
                connection.write(b"$GLOVITY,RX,STATS,RESET\r\n")
                reply = _wait_for_receiver_reply(
                    connection, b"$GLOVITY,RX,STATS,RESET,OK", 3.0
                )
                if reply is None:
                    print("警告：接收器未确认统计清零；请检查固件版本。")
                else:
                    print("接收器统计已清零。")
                connection.reset_input_buffer()
            deadline = None if args.duration <= 0 else time.monotonic() + args.duration
            next_report = time.monotonic() + 1.0
            while deadline is None or time.monotonic() < deadline:
                chunk = connection.read(max(connection.in_waiting, 1))
                if chunk:
                    if dump:
                        dump.write(chunk)
                    stats.total_bytes += len(chunk)
                    stats.printable_bytes += sum(byte in (9, 10, 13) or 32 <= byte < 127 for byte in chunk)
                    stats.magic_count += chunk.count(MAGIC)
                    if len(preview) < 512:
                        preview.extend(chunk[: 512 - len(preview)])
                    for frame in decoder.feed(chunk):
                        now = time.monotonic()
                        stats.valid_frames += 1
                        is_legacy = isinstance(frame, LegacyGloveFrame)
                        protocol = f"legacy-{len(frame.raw)}" if is_legacy else "v2-180"
                        if len(frame.raw) == 168:
                            stats.legacy_168_frames += 1
                        elif len(frame.raw) == 170:
                            stats.legacy_170_frames += 1
                        else:
                            stats.v2_frames += 1
                        frame_seq = (frame.header_5 | (frame.header_6 << 8)) if is_legacy else frame.frame_seq
                        if stats.last_seq is not None:
                            delta = (frame_seq - stats.last_seq) & 0xFFFF
                            if delta > 1:
                                stats.sequence_gaps += delta - 1
                        stats.last_seq = frame_seq
                        if stats.last_frame_at is not None and now > stats.last_frame_at:
                            recent_rates.append(1.0 / (now - stats.last_frame_at))
                        stats.last_frame_at = now
                        if args.show_frames:
                            hand = "左" if frame.hand_id == 0 else "右"
                            print(
                                f"帧#{stats.valid_frames} {hand}手 protocol={protocol} seq={frame_seq} "
                                f"time={getattr(frame, 'timestamp_us', 'N/A')} "
                                f"rec={getattr(frame, 'recording', 'N/A')} "
                                f"battery={frame.battery_percent}% "
                                f"fail={getattr(frame, 'imu_fail_mask', 'N/A')}"
                            )
                now = time.monotonic()
                if now >= next_report:
                    rate = sum(recent_rates) / len(recent_rates) if recent_rates else 0.0
                    print(
                        f"状态：{stats.total_bytes} bytes，有效帧={stats.valid_frames} "
                        f"[旧168={stats.legacy_168_frames}/旧170={stats.legacy_170_frames}/V2={stats.v2_frames}]，"
                        f"近期约{rate:.1f} Hz，序号缺口={stats.sequence_gaps}，"
                        f"坏候选={decoder.rejected_candidates}"
                    )
                    next_report = now + 1.0
            if args.receiver_stats:
                connection.write(b"$GLOVITY,RX,STATS\r\n")
                reply = _wait_for_receiver_reply(
                    connection, b"$GLOVITY,RX,STATS,", 5.0, dump
                )
                if reply is None:
                    print("警告：未收到接收器统计响应；请检查固件版本和串口连接。")
                else:
                    print("接收器统计：" + reply.decode("ascii").strip())
    except KeyboardInterrupt:
        print("用户停止探测。")
    except serial.SerialException as exc:
        print(f"串口错误：{exc}", file=sys.stderr)
        return 2
    finally:
        if dump:
            dump.close()
    elapsed = time.monotonic() - stats.started
    print(_diagnosis(stats, decoder, bytes(preview), elapsed))
    if args.save:
        print(f"原始数据已保存到：{Path(args.save).resolve()}")
    return 0 if stats.valid_frames else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="探测并区分Glovity旧版与180字节V2帧")
    parser.add_argument("--list", action="store_true", help="只列出串口")
    parser.add_argument("--port", help="串口名称，例如 COM5；只有一个串口时可省略")
    parser.add_argument("--baud", type=int, default=921600, help="波特率，默认921600")
    parser.add_argument("--duration", type=float, default=15.0, help="探测秒数；0表示持续运行")
    parser.add_argument("--save", help="可选：保存收到的全部原始字节")
    parser.add_argument("--show-frames", action="store_true", help="逐帧显示关键字段")
    parser.add_argument(
        "--receiver-stats", action="store_true",
        help="仅用于接收器COM口：同一连接开始时清零统计、结束时查询队列统计",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return print_ports() if args.list else run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
