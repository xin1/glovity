"""Command-line tools for decoding and aligning Glovity recordings."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .clock import ClockModel, SyncPoint, fit_clock_model
from .protocol import AutoStreamDecoder, frame_from_dict
from .resample import align_hand_at


def decode_command(args: argparse.Namespace) -> int:
    decoder = AutoStreamDecoder()
    count = 0
    with Path(args.output).open("w", encoding="utf-8") as output:
        with Path(args.input).open("rb") as source:
            while chunk := source.read(4096):
                for frame in decoder.feed(chunk):
                    output.write(json.dumps(frame.to_dict(args.device_id), ensure_ascii=False) + "\n")
                    count += 1
    print(json.dumps({"frames": count, "legacy_frames": decoder.legacy_frames,
                      "v2_frames": decoder.v2_frames,
                      "rejected": decoder.rejected_candidates,
                      "discarded_bytes": decoder.discarded_bytes}))
    return 0


def fit_command(args: argparse.Namespace) -> int:
    grouped: dict[str, list[SyncPoint]] = {}
    with Path(args.sync).open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            grouped.setdefault(row["device_id"], []).append(SyncPoint(int(row["glove_us"]), int(row["nokov_us"])))
    result = {}
    for device_id, points in grouped.items():
        model = fit_clock_model(points)
        result[device_id] = {
            "scale": model.scale,
            "offset_us": model.offset_us,
            "drift_ppm": model.drift_ppm,
            "rms_error_us": model.rms_error_us,
            "max_error_us": model.max_error_us,
            "point_count": model.point_count,
        }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def align_command(args: argparse.Namespace) -> int:
    model_values = json.loads(Path(args.models).read_text(encoding="utf-8"))
    models = {
        device_id: ClockModel(
            scale=float(value["scale"]),
            offset_us=float(value["offset_us"]),
            rms_error_us=float(value.get("rms_error_us", 0.0)),
            max_error_us=float(value.get("max_error_us", 0.0)),
            point_count=int(value.get("point_count", 0)),
        )
        for device_id, value in model_values.items()
    }
    frames = {device_id: [] for device_id in models}
    with Path(args.frames).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            device_id = value.get("device_id")
            if device_id in frames:
                frames[device_id].append(frame_from_dict(value))
            elif args.strict:
                raise ValueError(f"line {line_number}: no clock model for device {device_id!r}")

    timestamps: list[int] = []
    with Path(args.timeline).open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            timestamps.append(int(row["nokov_us"]))

    emitted = 0
    with Path(args.output).open("w", encoding="utf-8") as output:
        for timestamp in timestamps:
            record = {"nokov_timestamp_us": timestamp, "devices": {}}
            for device_id, model in models.items():
                aligned = align_hand_at(frames[device_id], model, timestamp, max_gap_us=args.max_gap_us)
                record["devices"][device_id] = aligned.to_dict() if aligned is not None else None
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            emitted += 1
    print(json.dumps({"timestamps": emitted, "devices": {key: len(value) for key, value in frames.items()}}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Glovity frame alignment tools")
    commands = parser.add_subparsers(dest="command", required=True)
    decode = commands.add_parser("decode", help="decode a raw USB byte stream to JSON Lines")
    decode.add_argument("input")
    decode.add_argument("output")
    decode.add_argument("--device-id", required=True)
    decode.set_defaults(func=decode_command)
    fit = commands.add_parser("fit-clock", help="fit device clocks from a sync-point CSV")
    fit.add_argument("sync", help="CSV columns: device_id,glove_us,nokov_us")
    fit.add_argument("output")
    fit.set_defaults(func=fit_command)
    align = commands.add_parser("align", help="resample decoded hands on a NOKOV timeline")
    align.add_argument("frames", help="combined decoded JSON Lines for all devices")
    align.add_argument("models", help="JSON produced by fit-clock")
    align.add_argument("timeline", help="CSV with a nokov_us column")
    align.add_argument("output")
    align.add_argument("--max-gap-us", type=int, default=100_000)
    align.add_argument("--strict", action="store_true", help="reject frames whose device has no clock model")
    align.set_defaults(func=align_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
