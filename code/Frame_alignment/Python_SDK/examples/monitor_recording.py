#!/usr/bin/env python3
"""Monitor Glovity protocol/version and REC state without opening a camera."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from telex.hub.parse import next_receiver_frame_candidate, parse_glove_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor glove REC state")
    parser.add_argument("--port", required=True, help="COM port, for example COM5")
    parser.add_argument("--baud", type=int, default=921600)
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is missing: python -m pip install pyserial", file=sys.stderr)
        return 2

    buffer = bytearray()
    # Track each hand independently.  A single shared key makes alternating
    # left/right frames look like a state change and floods the terminal.
    last_key_by_hand = {}
    last_report_by_hand = {}
    count = 0
    print(f"Listening on {args.port} @ {args.baud}; Ctrl+C to stop")
    try:
        with serial.Serial(args.port, args.baud, timeout=0.2) as port:
            port.reset_input_buffer()
            while True:
                chunk = port.read(max(port.in_waiting, 1))
                if chunk:
                    buffer.extend(chunk)
                while True:
                    candidate = next_receiver_frame_candidate(buffer)
                    if candidate is None:
                        break
                    raw, size = candidate
                    frame = parse_glove_frame(raw, timestamp_s=time.monotonic())
                    if frame is None:
                        del buffer[:1]
                        continue
                    del buffer[:size]
                    count += 1
                    recording = frame.recording if frame.has_recording_flag else None
                    key = (frame.protocol, recording)
                    now = time.monotonic()
                    last_key = last_key_by_hand.get(frame.hand)
                    last_report = last_report_by_hand.get(frame.hand, 0.0)
                    if key != last_key or now - last_report >= 1.0:
                        rec_text = "N/A (legacy)" if recording is None else str(recording)
                        ts_text = str(frame.timestamp_us) if frame.has_device_timestamp else "N/A"
                        print(
                            f"frames={count} hand={frame.hand} protocol={frame.protocol} "
                            f"seq={frame.frame_seq} timestamp_us={ts_text} "
                            f"recording={rec_text} battery={frame.battery_percent}% "
                            f"fail_mask={frame.imu_fail_mask if frame.has_fail_mask else 'N/A'}"
                        )
                        last_key_by_hand[frame.hand] = key
                        last_report_by_hand[frame.hand] = now
    except KeyboardInterrupt:
        print("Stopped")
        return 0
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
