"""python -m awtest.tactile --help"""
import argparse
import json
import math
import threading
import time
from pathlib import Path

from .mapping import Mapper, MappingConfig, Route, Sample
from .paxini import PaxiniSource
from .session import Session, run_source


def load_config(path, channel):
    if not path:
        return MappingConfig(routes=(Route("finger", channel),))
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    obj["routes"] = tuple(Route(**r) for r in obj["routes"])
    return MappingConfig(**obj)


def main():
    p = argparse.ArgumentParser(description="PX-6AX → AW86234 触觉反馈；无 motor-port 时绝不驱动马达")
    p.add_argument("--sensor-port")
    p.add_argument("--motor-port")
    p.add_argument("--device-id", type=int, default=3)
    p.add_argument("--channel", type=int, default=4)
    p.add_argument("--points", type=int, default=0)
    p.add_argument("--allow-err-len", action="store_true", help="仅用于已验证状态1兼容的转接板")
    p.add_argument("--config")
    p.add_argument("--seconds", type=float, default=10.)
    p.add_argument("--log", help="JSONL 原始力、输出电平和诊断日志")
    p.add_argument("--demo", action="store_true", help="离线合成输入；禁止连接实际马达")
    args = p.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600:
        p.error("seconds must be 0..3600")
    if args.demo and args.motor_port:
        p.error("demo is offline only")
    if not args.demo and not args.sensor_port:
        p.error("specify sensor-port or demo")
    if args.sensor_port and args.motor_port and args.sensor_port.upper() == args.motor_port.upper():
        p.error("sensor and motor ports must differ")
    mapper = Mapper(load_config(args.config, args.channel))
    if not args.demo and {r.sensor for r in mapper.config.routes} != {"finger"}:
        p.error("single-board CLI routes must use sensor='finger'; multi-sensor applications use Mapper/Session API")
    if any(r.taxels for r in mapper.config.routes) and not args.points:
        p.error("taxel routes require --points")
    log = open(args.log, "w", encoding="utf-8") if args.log else None
    count = 0
    def report(row):
        nonlocal count
        row["input"] = {k: {"force_n": s.force, "sequence": s.sequence,
                             "acquired": s.acquired, "taxels_n": s.taxels}
                        for k, s in mapper.samples.items()}
        line = json.dumps(row, ensure_ascii=False)
        if log:
            log.write(line + "\n")
            log.flush()
        if count % 10 == 0:
            print(line, flush=True)
        count += 1
    device = None
    try:
        if args.demo:
            t0 = time.monotonic()
            for n in range(int(args.seconds * 50)):
                t = n / 50
                force = 0. if t < .5 or t > args.seconds-.5 else min(10., 2 * (t-.5))
                for sensor in {r.sensor for r in mapper.config.routes}:
                    mapper.ingest(Sample(sensor, n, t0+t, (0., 0., force)))
                levels, details = mapper.render(t0+t)
                report({"time": t0+t, "levels": levels, **details})
        else:
            source = PaxiniSource(args.sensor_port, args.device_id, points=args.points,
                                  allow_err_len=args.allow_err_len)
            if args.motor_port:
                from ..device import AWTestDevice
                device = AWTestDevice()
                device.connect(args.motor_port)
                run_source(source, Session(device, mapper), threading.Event(), report, args.seconds)
            else:
                end = time.monotonic() + args.seconds
                with source:
                    while time.monotonic() < end:
                        mapper.ingest(source.read())
                        levels, details = mapper.render(time.monotonic())
                        report({"time": time.monotonic(), "levels": levels, **details})
                        time.sleep(.02)
        print(f"completed {count} frames; motor_output={bool(args.motor_port)}")
    finally:
        if device:
            device.disconnect()
        if log:
            log.close()


if __name__ == "__main__":
    main()
