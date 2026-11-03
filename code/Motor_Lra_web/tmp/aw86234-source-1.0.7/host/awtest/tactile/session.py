"""Single-owner haptic session. Only fresh sensor samples renew motor output."""
import time

from .mapping import Mapper
from ..protocol import commands as C


class Session:
    def __init__(self, device, mapper: Mapper, clock=time.monotonic, telemetry=False):
        self.device, self.mapper, self.clock = device, mapper, clock
        self.active = False
        self.sequence = 0
        self.failed = False
        self.last_sent = {}
        self.telemetry = telemetry
        self._next_diagnostic = 0.
        self._diagnostic = {}

    def start(self):
        if self.active or self.failed:
            raise RuntimeError("session already started or faulted; create a new session")
        caps = self.device.info.caps
        if not caps or not caps.has(C.CAP_TACTILE):
            raise RuntimeError("触觉反馈需要固件 1.0.4 / CAP_TACTILE，请先升级固件")
        if self.mapper.mask & ~self.device.info.present_mask:
            raise RuntimeError("映射通道未全部在线")
        if self.device.get_status().active_mask:
            raise RuntimeError("请先停止现有播放，再开始触觉反馈")
        # Require a valid current frame before creating a silent session.
        self.mapper.render(self.clock())
        try:
            self.device.tactile_frame(self.mapper.mask, self.sequence, (0,) * 8, start=True)
            self.active = True
        except Exception:
            self.failed = True
            self.device.stop_all(1)
            raise

    def step(self):
        if not self.active or self.failed:
            raise RuntimeError("tactile session is not active")
        try:
            levels, details = self.mapper.render(self.clock())
            current = {key: sample.sequence for key, sample in self.mapper.samples.items()}
            # Repeated rendering must not manufacture fresh input / firmware leases.
            if current == self.last_sent:
                return None
            self.sequence = (self.sequence + 1) & 65535
            self.device.tactile_frame(self.mapper.mask, self.sequence, levels)
            self.last_sent = current
            # Keep optional diagnostic I/O out of the sharp pulse's ON interval.
            click_on = self.mapper.config.pattern in ("pulse","double") and any(levels)
            if self.telemetry and not click_on and self.clock() >= self._next_diagnostic:
                diagnostic = {}
                for route in self.mapper.config.routes:
                    level = self.device.reg_read_u8(route.channel,0x1c)
                    data = self.device.reg_read(route.channel,0x26,2)
                    if len(data) != 2: raise ValueError("invalid driver diagnostic response")
                    raw = int.from_bytes(data,"big")
                    diagnostic[str(route.channel)] = {"level":level & 127,"tracking":bool(level & 128),
                        "cont_raw":raw,"counter_hz":384000/raw if raw and levels[route.channel] else None,
                        "time":self.clock()}
                self._diagnostic = diagnostic
                self._next_diagnostic = self.clock()+.25
            return {"time": self.clock(), "levels": levels, "driver":self._diagnostic, **details}
        except Exception:
            self.failed = True
            self.stop()
            raise

    def stop(self):
        was_active, self.active = self.active, False
        if was_active:
            self.device.stop_all(1)


def run_source(source, session, stop, report=lambda value: None, duration=None):
    """No backlog, no replay after timeout. Run in a worker, never the GUI thread."""
    start = time.monotonic()
    try:
        with source:
            if stop.is_set():
                return
            session.mapper.ingest(source.read())
            if stop.is_set():
                return
            session.start()
            if stop.is_set():
                return
            # start() validated/rendered the first sample: emit it before consuming
            # a second sample, otherwise a short onset can disappear during START.
            result = session.step()
            if result: report(result)
            time.sleep(.02)
            while not stop.is_set() and (duration is None or time.monotonic()-start < duration):
                tick = time.monotonic()
                session.mapper.ingest(source.read())
                if stop.is_set():
                    break
                result = session.step()
                if result:
                    report(result)
                # Python's high-resolution Windows sleep avoids Event.wait's
                # ~15.6ms clock quantization (20ms requested became ~31ms).
                # Cancellation still checked before I/O and after each <=20ms sleep.
                time.sleep(max(0., .02 - (time.monotonic() - tick)))
    finally:
        session.stop()
