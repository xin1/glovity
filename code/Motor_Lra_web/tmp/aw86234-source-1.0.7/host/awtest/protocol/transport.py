# -*- coding: utf-8 -*-
"""串口传输层：后台读线程、按 SEQ 匹配的请求-应答、事件分发、心跳、断线检测。

* **不依赖任何 GUI 模块**（可被 CLI、pytest、脚本引擎直接使用）
* UI 侧通过回调把帧投递到 Qt 信号即可（Qt 允许从非 GUI 线程 emit 信号）
* 严格按协议 §3.1 的要求：UI 绝不阻塞、按 SEQ 匹配、事件与应答可交错

移植自 ``reference/protocol_reference.py`` 的帧层，传输部分按 `05` 文档 §3 实现。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import commands as C
from .framing import (CHAN_NONE, FLAG_EVENT, FLAG_NORESP, FLAG_RESPONSE,
                      Frame, FrameParser)

try:  # pyserial 是运行期依赖，但允许在没有它的环境里 import 本模块（便于单测）
    import serial
    from serial.tools import list_ports as _list_ports
    HAVE_SERIAL = True
except Exception:  # pragma: no cover
    serial = None
    _list_ports = None
    HAVE_SERIAL = False


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class TransportError(RuntimeError):
    """传输层错误（串口打不开、已断开等）。"""


class RequestTimeout(TransportError):
    """请求在超时时间内没有收到应答。"""

    def __init__(self, cmd: int, seq: int, timeout: float):
        super().__init__(f"{C.cmd_name(cmd)} 请求超时（seq={seq}, {timeout * 1000:.0f} ms）")
        self.cmd = cmd
        self.seq = seq
        self.timeout = timeout


class DeviceError(RuntimeError):
    """设备返回错误应答（协议 §6）。"""

    def __init__(self, cmd: int, chan: int, errcode: int, text: str = ""):
        self.cmd = cmd
        self.chan = chan
        self.errcode = errcode
        self.text = text
        detail = C.err_cn(errcode)
        msg = f"{C.cmd_name(cmd)} 失败：{C.err_name(errcode)}（{detail}）"
        if text:
            msg += f" —— {text}"
        super().__init__(msg)


# --------------------------------------------------------------------------
# 端口信息
# --------------------------------------------------------------------------


@dataclass
class PortInfo:
    device: str
    description: str = ""
    hwid: str = ""
    vid: Optional[int] = None
    pid: Optional[int] = None

    @property
    def is_espressif(self) -> bool:
        return self.vid == 0x303A

    def label(self) -> str:
        tag = " [Espressif USB-JTAG]" if self.is_espressif else ""
        desc = f" · {self.description}" if self.description else ""
        return f"{self.device}{desc}{tag}"


def list_ports(only_espressif: bool = False) -> List[PortInfo]:
    """枚举串口。USB-Serial-JTAG（VID 0x303A）排在最前。"""
    if not HAVE_SERIAL:
        return []
    out: List[PortInfo] = []
    for p in _list_ports.comports():
        vid = pid = None
        try:
            if p.vid is not None:
                vid, pid = int(p.vid), int(p.pid)
        except Exception:
            pass
        info = PortInfo(device=p.device, description=p.description or "",
                        hwid=p.hwid or "", vid=vid, pid=pid)
        if only_espressif and not info.is_espressif:
            continue
        out.append(info)
    out.sort(key=lambda i: (not i.is_espressif, i.device))
    return out


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------


@dataclass
class Stats:
    opened_at: float = 0.0
    frames_tx: int = 0
    frames_rx: int = 0
    bytes_tx: int = 0
    bytes_rx: int = 0
    crc_errors: int = 0
    resyncs: int = 0
    timeouts: int = 0
    device_errors: int = 0
    more_violations: int = 0
    ver_violations: int = 0
    ascii_lines: int = 0
    last_rtt_ms: float = 0.0
    rtt_history: List[float] = field(default_factory=list)

    def note_rtt(self, ms: float, keep: int = 50) -> None:
        self.last_rtt_ms = ms
        self.rtt_history.append(ms)
        if len(self.rtt_history) > keep:
            del self.rtt_history[:len(self.rtt_history) - keep]

    @property
    def rtt_avg_ms(self) -> float:
        return sum(self.rtt_history) / len(self.rtt_history) if self.rtt_history else 0.0


# --------------------------------------------------------------------------
# 传输层
# --------------------------------------------------------------------------


class SerialTransport:
    """一个串口连接 + 一条后台读线程。

    回调（都在读线程里被调用，UI 侧需自行切回主线程）：

    * ``on_frame(frame, direction)`` —— 每个完整协议帧（'rx' / 'tx'）
    * ``on_event(frame)`` —— 设备主动上报的事件帧
    * ``on_ascii(line)`` —— ASCII 模式下的文本行
    * ``on_log(level, text)`` —— 人类可读日志
    * ``on_disconnect(reason)`` —— 断线
    """

    def __init__(
        self,
        on_frame: Optional[Callable[[Frame, str], None]] = None,
        on_event: Optional[Callable[[Frame], None]] = None,
        on_ascii: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.on_frame = on_frame
        self.on_event = on_event
        self.on_ascii = on_ascii
        self.on_log = on_log
        self.on_disconnect = on_disconnect

        self.stats = Stats()
        self.port: Optional[str] = None
        self.baudrate: int = 115200
        self.binary_mode: bool = False

        self._ser = None
        self._parser = FrameParser()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._pending: Dict[int, Tuple[threading.Event, List[Frame]]] = {}
        self._pending_lock = threading.Lock()
        self._seq = 0
        self._ascii_buf = bytearray()

        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        self._hb_period = 1.0

    # ---- 生命周期 ----

    @property
    def is_open(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def open(self, port: str, baudrate: int = 115200, read_timeout: float = 0.05) -> None:
        if not HAVE_SERIAL:
            raise TransportError("未安装 pyserial，无法打开串口")
        self.close()
        try:
            # ⚠️ 本板是 ESP32-C6 的 **USB-Serial-JTAG**：pyserial 打开端口时默认会拉
            #    DTR/RTS，芯片检测到该跳变会**复位重启**（固件 1~2 s 后才起来）。
            #    先把 DTR/RTS 置为 False 再 open()，尽量避免这次复位。
            ser = serial.Serial()
            ser.port = port
            ser.baudrate = baudrate
            ser.timeout = read_timeout
            ser.write_timeout = 1.0
            ser.dtr = False
            ser.rts = False
            ser.open()
            self._ser = ser
        except Exception as exc:  # pragma: no cover - 依赖真实硬件
            self._ser = None
            raise TransportError(f"打开串口 {port} 失败：{exc}") from exc

        self.port = port
        self.baudrate = baudrate
        self.stats = Stats(opened_at=time.time())
        self._parser = FrameParser()
        self.binary_mode = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, name="awtest-rx", daemon=True)
        self._thread.start()
        self._log("info", f"串口已打开：{port} @ {baudrate}")

    def close(self, send_stop: bool = False) -> None:
        if send_stop and self.is_open and self.binary_mode:
            try:
                self.stop_all(immediate=1)
                self.trig_park()
            except Exception:
                pass
        self._stop_heartbeat()
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=0.5)
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self.binary_mode = False
        with self._pending_lock:
            for ev, _bucket in self._pending.values():
                ev.set()
            self._pending.clear()

    # ---- 读线程 ----

    def _read_loop(self) -> None:  # pragma: no cover - 需要真实串口
        while not self._stop.is_set():
            try:
                ser = self._ser
                if ser is None:
                    break
                # Block only for the first byte, then drain what is already
                # available. Waiting for 256 bytes delays a 22-byte ACK by the
                # full 50ms timeout and caps real-time control near 16Hz.
                chunk = ser.read(max(1, min(256, ser.in_waiting)))
            except Exception as exc:
                if not self._stop.is_set():
                    self._log("error", f"串口读取异常：{exc}")
                    self._notify_disconnect(f"串口读取异常：{exc}")
                break
            if not chunk:
                continue
            self.stats.bytes_rx += len(chunk)
            if self.binary_mode:
                self._consume_binary(chunk)
            else:
                self._consume_ascii(chunk)
        if not self._stop.is_set():
            self._notify_disconnect("读线程结束（设备可能已拔出）")

    def _consume_ascii(self, chunk: bytes) -> None:
        if chunk[:2] == b"\xaa\x55":
            self._log("warn", "检测到二进制帧但当前处于 ASCII 模式，请先发送 MODE BIN")
        self._ascii_buf.extend(chunk)
        while b"\n" in self._ascii_buf:
            line, _, rest = self._ascii_buf.partition(b"\n")
            self._ascii_buf = bytearray(rest)
            text = line.decode("utf-8", errors="replace").rstrip("\r")
            if not text:
                continue
            self.stats.ascii_lines += 1
            if self.on_ascii:
                self.on_ascii(text)
            self._log("rx", f"[ASCII] {text}")

    def _consume_binary(self, chunk: bytes) -> None:
        for frame in self._parser.feed(chunk):
            self.stats.frames_rx += 1
            if frame.more:
                self.stats.more_violations += 1
                self._log("error", "协议违例：收到 MORE=1 的帧（v1.0 设备不得置位）")
            if self.on_frame:
                self.on_frame(frame, "rx")
            if frame.flags & FLAG_EVENT:
                if self.on_event:
                    self.on_event(frame)
                continue
            if frame.flags & FLAG_RESPONSE:
                with self._pending_lock:
                    bucket = self._pending.get(frame.seq)
                if bucket is not None:
                    bucket[1].append(frame)
                    bucket[0].set()
                else:
                    self._log("warn", f"收到无人等待的应答：seq={frame.seq} "
                                      f"cmd={C.cmd_name(frame.cmd)}")
        self.stats.crc_errors = self._parser.crc_errors
        self.stats.resyncs = self._parser.resyncs

    def _notify_disconnect(self, reason: str) -> None:
        self._log("error", f"串口断开：{reason}")
        if self.on_disconnect:
            self.on_disconnect(reason)

    def _log(self, level: str, text: str) -> None:
        if self.on_log:
            self.on_log(level, text)

    # ---- 发送 ----

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        if self._seq == 0:
            self._seq = 1        # SEQ 0 保留给事件帧
        return self._seq

    def write_raw(self, data: bytes, note: str = "") -> None:
        ser = self._ser
        if ser is None:
            raise TransportError("串口未打开")
        with self._write_lock:
            ser.write(data)
            ser.flush()
        self.stats.bytes_tx += len(data)
        if note:
            self._log("tx", note)

    def send_ascii(self, line: str) -> None:
        """ASCII 模式下发送一行文本。"""
        self.write_raw((line.rstrip("\r\n") + "\n").encode("utf-8"), f"[ASCII] {line}")

    def send_frame(self, cmd: int, payload: bytes = b"", chan: int = CHAN_NONE,
                   noresp: bool = False, seq: Optional[int] = None) -> Frame:
        """发送一个请求帧（不等待应答）。"""
        f = Frame.request(cmd, payload, seq=self._next_seq() if seq is None else seq,
                          chan=chan, noresp=noresp)
        self.write_raw(f.to_bytes())
        self.stats.frames_tx += 1
        if self.on_frame:
            self.on_frame(f, "tx")
        return f

    def request(self, cmd: int, payload: bytes = b"", chan: int = CHAN_NONE,
                timeout: float = 0.5) -> Frame:
        """发送请求并等待应答；超时抛 ``RequestTimeout``，错误应答抛 ``DeviceError``。"""
        if not self.is_open:
            raise TransportError("串口未打开")
        f = Frame.request(cmd, payload, seq=self._next_seq(), chan=chan)
        ev = threading.Event()
        bucket: List[Frame] = []
        with self._pending_lock:
            self._pending[f.seq] = (ev, bucket)
        t0 = time.perf_counter()
        try:
            self.send_frame(cmd, payload, chan=chan, seq=f.seq)
            if not ev.wait(timeout):
                self.stats.timeouts += 1
                raise RequestTimeout(cmd, f.seq, timeout)
        finally:
            with self._pending_lock:
                self._pending.pop(f.seq, None)
        rtt = (time.perf_counter() - t0) * 1000.0
        self.stats.note_rtt(rtt)
        if not bucket:
            self.stats.timeouts += 1
            raise RequestTimeout(cmd, f.seq, timeout)
        resp = bucket[0]
        if resp.is_error:
            self.stats.device_errors += 1
            raise DeviceError(resp.cmd, resp.chan, resp.err_code_safe, resp.error_text)
        return resp

    # ---- 模式切换 ----

    def enter_binary_mode(self, timeout: float = 5.0) -> bool:
        """按 `05` 文档 §3.3：先试二进制 PING，失败再发 ``MODE BIN``。

        返回 True 表示当前处于二进制模式。

        ⚠️ **本板需要重试**：ESP32-C6 的 USB-Serial-JTAG 在宿主打开串口时可能复位芯片，
        固件要 1~2 s 才起来；单次"发 MODE BIN 等 1 s"会在这个窗口内失败。
        因此这里在 timeout（默认 5 s）内反复重试，覆盖整个启动窗口。
        """
        if not self.is_open:
            raise TransportError("串口未打开")

        deadline = time.time() + max(1.0, timeout)
        retried = False
        while time.time() < deadline:
            # 1) 也许固件已经在二进制模式
            try:
                self.binary_mode = True
                self._parser.reset()
                self.request(C.CMD_PING, b"", timeout=0.3)
                self._log("info", "固件已处于二进制模式")
                return True
            except Exception:
                pass

            # 2) 切回 ASCII 再发 MODE BIN
            self.binary_mode = False
            self._parser.reset()
            self._ascii_buf.clear()
            got_ok = threading.Event()

            def _watch(line: str) -> None:
                if "proto=binary" in line.replace(" ", ""):
                    got_ok.set()

            prev = self.on_ascii
            self.on_ascii = lambda line: (_watch(line), prev(line) if prev else None)[-1]
            try:
                # ★ 先发一个空行。上面的二进制 PING 在固件仍处 ASCII 模式时，
                #   其帧头之外的字节（0x55/0x01/…）会**残留在固件的行缓冲里**，
                #   把紧随其后的 "MODE BIN" 拼成"<乱码>MODE BIN" → 固件回 ERR_UNKNOWN_CMD。
                #   发一个裸换行可以把这半截行截断掉。
                self.send_ascii("")
                time.sleep(0.05)
                self.send_ascii("MODE BIN")
                got_ok.wait(0.5)
            finally:
                self.on_ascii = prev

            self.binary_mode = True
            self._parser.reset()
            try:
                self.request(C.CMD_PING, b"", timeout=0.5)
                self._log("info", "已切换到二进制模式")
                return True
            except Exception:
                if not retried:
                    self._log("info", "固件尚未就绪（可能刚被串口打开复位），正在重试…")
                    retried = True
                time.sleep(0.3)

        self._log("error", "切换到二进制模式失败（超时）")
        return False

    # ---- 心跳 ----

    def start_heartbeat(self, period_s: float, use_noresp: bool = True) -> None:
        """启动心跳。**按 `05` 文档 §7 不得提供关闭选项**——停掉心跳会让固件 5 s 后停机。"""
        self._stop_heartbeat()
        self._hb_period = max(0.05, period_s)
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop,
                                           name="awtest-hb", daemon=True)
        self._hb_thread.start()

    def _stop_heartbeat(self) -> None:
        self._hb_stop.set()
        t, self._hb_thread = self._hb_thread, None
        if t is not None and t.is_alive():
            t.join(timeout=0.5)

    def _heartbeat_loop(self) -> None:  # pragma: no cover - 时间相关
        while not self._hb_stop.wait(self._hb_period):
            if not self.is_open or not self.binary_mode:
                continue
            try:
                if self._hb_period >= 0.5:
                    self.request(C.CMD_PING, b"", timeout=0.4)
                else:
                    self.send_frame(C.CMD_PING, b"", noresp=True)
            except Exception:
                pass

    # ---- 常用便捷命令（供设备层与安全退出路径使用） ----

    def stop_all(self, immediate: int = 1) -> None:
        self.request(C.CMD_STOP_ALL, bytes([immediate & 1]), timeout=0.5)

    def trig_park(self) -> None:
        self.request(C.CMD_TRIG_PARK, b"", timeout=0.5)

    def ping(self) -> Frame:
        return self.request(C.CMD_PING, b"", timeout=0.4)
