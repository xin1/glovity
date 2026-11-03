# -*- coding: utf-8 -*-
"""``awtest.ui`` 公共基础设施。

本模块提供界面层共用的四件东西（各页面都从这里取，避免重复实现）：

* :class:`DeviceBridge` —— 把 ``SerialTransport`` 在工作线程里触发的回调
  转成 Qt 信号（跨线程自动走 ``Qt.QueuedConnection``），UI 只连信号不碰串口。
* :class:`TaskRunner` —— 一个专职工作线程 + 优先级队列，所有**阻塞式**设备调用
  都在它里面执行，保证 UI 线程永不阻塞；``STOP_ALL`` 这类安全操作可插队。
* :class:`UiContext` —— 页面共享的上下文（设备、任务、能力位、通道勾选、日志、确认框）。
* :func:`cap_reason` 等能力位文案 —— 说明"为什么不可用"（规格书 §5.3 硬要求）。

同时在这里探测可选模块 ``awtest.waveform`` / ``awtest.script``（由另一位工程师并行实现），
缺失时置 ``HAVE_WAVEFORM`` / ``HAVE_SCRIPT`` 为 False，界面据此优雅降级而不是崩溃。
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from awtest.device import AWTestDevice, ConnectionInfo
from awtest.protocol import codec as K
from awtest.protocol import commands as C
from awtest.protocol.framing import Frame
from awtest.protocol.transport import (DeviceError, RequestTimeout,
                                       TransportError)

# --------------------------------------------------------------------------
# 可选模块探测（接口约定 §2 / §3）
# --------------------------------------------------------------------------

waveform = None
script = None
WAVEFORM_ERROR = ""
SCRIPT_ERROR = ""

try:  # pragma: no cover - 取决于并行开发进度
    from awtest.waveform import editor_model as waveform_editor
    from awtest.waveform import generator as waveform_gen
    from awtest.waveform import io_csv as waveform_csv
    waveform = True
    HAVE_WAVEFORM = True
except Exception as exc:  # ImportError 或模块内部错误
    waveform_editor = waveform_gen = waveform_csv = None
    waveform = None
    HAVE_WAVEFORM = False
    WAVEFORM_ERROR = f"{type(exc).__name__}: {exc}"

try:  # pragma: no cover - 取决于并行开发进度
    from awtest.script import engine as script_engine
    from awtest.script import report as script_report
    from awtest.script import schema as script_schema
    script = True
    HAVE_SCRIPT = True
except Exception as exc:
    script_engine = script_report = script_schema = None
    script = None
    HAVE_SCRIPT = False
    SCRIPT_ERROR = f"{type(exc).__name__}: {exc}"


def missing_module_hint(which: str) -> str:
    """缺失模块时给用户看的中文提示。"""
    if which == "waveform":
        return ("波形模块 awtest.waveform 尚未就绪"
                + (f"（{WAVEFORM_ERROR}）" if WAVEFORM_ERROR else "")
                + "。该模块由波形引擎提供生成/编辑/CSV 能力，"
                  "在它到位之前，本页的生成、编辑、下发按钮不可用。")
    return ("测试脚本模块 awtest.script 尚未就绪"
            + (f"（{SCRIPT_ERROR}）" if SCRIPT_ERROR else "")
            + "。该模块提供脚本解析与执行引擎，在它到位之前无法运行测试脚本。")


# --------------------------------------------------------------------------
# 日志级别与颜色（日志页、状态栏共用）
# --------------------------------------------------------------------------

LOG_LEVELS = ("debug", "info", "warn", "error", "rx", "tx", "user", "evt")

LOG_COLORS = {
    "debug": "#7f8c8d",
    "info": "#d0d0d0",
    "warn": "#e6b422",
    "error": "#ff5555",
    "rx": "#5fa8d3",
    "tx": "#8fbf6f",
    "user": "#c9a0dc",
    "evt": "#d7a3ff",
}

LOG_LEVEL_CN = {
    "debug": "调试", "info": "信息", "warn": "警告", "error": "错误",
    "rx": "接收", "tx": "发送", "user": "操作", "evt": "事件",
}


def fmt_hex(value: int, width: int = 2) -> str:
    return f"0x{value:0{width}X}"


def mask_to_list(mask: int, count: int = C.CHANNEL_COUNT) -> List[int]:
    return [ch for ch in range(count) if mask & (1 << ch)]


def mask_to_text(mask: int, count: int = C.CHANNEL_COUNT) -> str:
    chans = mask_to_list(mask, count)
    return "、".join(f"CH{ch}" for ch in chans) if chans else "（无）"


def error_text(exc: BaseException) -> str:
    """把设备/传输异常翻成一句中文，错误应答的说明文本一并透传（协议 §6）。"""
    if isinstance(exc, DeviceError):
        msg = f"{C.cmd_name(exc.cmd)}：{C.err_name(exc.errcode)}（{C.err_cn(exc.errcode)}）"
        if exc.text:
            msg += f" —— {exc.text}"
        if exc.chan not in (0xFE, 0xFF, None):
            msg += f" [CH{exc.chan}]"
        return msg
    if isinstance(exc, RequestTimeout):
        return f"请求超时：{exc}"
    if isinstance(exc, TransportError):
        return f"传输错误：{exc}"
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# 能力位文案（规格书 §5.3：不可用必须写明原因）
# --------------------------------------------------------------------------

CAP_DISABLED_REASONS: Dict[int, str] = {
    C.CAP_MOTOR_POWER_GPIO:
        "本板 AW86234 的 VDD 由 SW3 手动控制，固件无法断电。复位只能靠软复位（0x00=0xAA）"
        "或人工拨 SW3。",
    C.CAP_RSTN_GPIO:
        "本板 RSTN 直接并 VDD，芯片常使能，没有硬件复位引脚可用。",
    C.CAP_RTP_48K:
        "经 TCA9548A 与 ESP32-C6 都只到 400 kHz，48K RTP 需 ≥1 MHz，本板不可用。",
    C.CAP_TCA_ONLINE:
        "TCA9548A（0x70）无应答，8 路分路器不可用，仅单路直连模式可用。",
}

CAP_DEGRADED_REASONS: Dict[int, str] = {
    C.CAP_INTERNAL_PULLUP:
        "上游 I2C 上拉不足，正在使用内部弱上拉 + 降速运行。建议补焊 4.7 kΩ 上拉到 3.3 V。",
    C.CAP_SHARED_TRIG_567:
        "CH5/CH6/CH7 共用 TRIG（GPIO14），硬触发下三路只能同时动作。",
}


def cap_reason(bit: int, available: bool) -> str:
    """返回能力位的"可用/不可用/降级"说明（不可用时写明原因）。"""
    if not available:
        return CAP_DISABLED_REASONS.get(bit, "固件报告该能力不可用，相关控件已禁用。")
    return CAP_DEGRADED_REASONS.get(bit, "")


# --------------------------------------------------------------------------
# 设备桥：工作线程回调 → Qt 信号
# --------------------------------------------------------------------------


class DeviceBridge(QObject):
    """把 ``SerialTransport`` 的回调转成 Qt 信号。

    回调在**串口读线程**里被调用，Qt 会自动把跨线程的 emit 变成队列投递，
    所以槽函数一定在 UI 线程执行 —— 界面不需要自己加锁。
    """

    frame_received = Signal(object, str)   # Frame, 'rx'|'tx'
    event_received = Signal(object)        # Frame（事件帧）
    ascii_received = Signal(str)
    log_received = Signal(str, str)        # level, text
    disconnected = Signal(str)             # reason

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._attached = False

    # ---- 挂钩 ----

    def attach(self, device: Optional[AWTestDevice]) -> None:
        """把回调挂到设备的传输层上（重复调用安全）。"""
        if device is None:
            return
        t = device.t
        t.on_frame = self._on_frame
        t.on_event = self._on_event
        t.on_ascii = self._on_ascii
        t.on_log = self._on_log
        t.on_disconnect = self._on_disconnect
        self._attached = True

    # ---- 回调（工作线程） ----

    def _on_frame(self, frame: Frame, direction: str) -> None:
        self.frame_received.emit(frame, direction)

    def _on_event(self, frame: Frame) -> None:
        self.event_received.emit(frame)

    def _on_ascii(self, line: str) -> None:
        self.ascii_received.emit(line)

    def _on_log(self, level: str, text: str) -> None:
        self.log_received.emit(level, text)

    def _on_disconnect(self, reason: str) -> None:
        self.disconnected.emit(reason)


# --------------------------------------------------------------------------
# 任务线程：所有阻塞式设备调用都在这里跑
# --------------------------------------------------------------------------


class TaskRunner(QObject):
    """专职工作线程 + 优先级队列。

    ``submit`` 的任务函数签名固定为 ``fn(progress) -> result``：

    * ``progress(payload)`` 可以由任务在任意时刻调用，回到 UI 线程的
      ``on_progress`` 里；
    * 任务抛出的任何异常都会转成 ``on_error(中文说明)``，不会崩界面。

    ``urgent=True`` 的任务插到队首（安全相关的 ``STOP_ALL`` / ``TRIG_PARK`` 用）。
    """

    completed = Signal(int, object)
    failed = Signal(int, str)
    progressed = Signal(int, object)
    busy_changed = Signal(int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cv = threading.Condition()
        self._queue: List[Tuple[int, int, int, Callable]] = []
        self._callbacks: Dict[int, Tuple[Optional[Callable], Optional[Callable],
                                         Optional[Callable]]] = {}
        self._tid = 0
        self._order = 0
        self._busy = 0
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="awtest-ui-task",
                                        daemon=True)
        self._thread.start()
        self.completed.connect(self._on_completed)
        self.failed.connect(self._on_failed)
        self.progressed.connect(self._on_progressed)

    # ---- 对外 ----

    @property
    def busy(self) -> int:
        return self._busy

    def submit(self, fn: Callable[[Callable[[Any], None]], Any],
               on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[str], None]] = None,
               on_progress: Optional[Callable[[Any], None]] = None,
               urgent: bool = False) -> int:
        with self._cv:
            self._tid += 1
            self._order += 1
            tid = self._tid
            self._callbacks[tid] = (on_done, on_error, on_progress)
            item = (0 if urgent else 1, self._order, tid, fn)
            if urgent:
                idx = 0
                while idx < len(self._queue) and self._queue[idx][0] == 0:
                    idx += 1
                self._queue.insert(idx, item)
            else:
                self._queue.append(item)
            self._busy += 1
            self._cv.notify_all()
        self.busy_changed.emit(self._busy)
        return tid

    def shutdown(self, timeout: float = 0.5) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        t = self._thread
        if t.is_alive():
            t.join(timeout=timeout)

    # ---- 工作线程 ----

    def _loop(self) -> None:  # pragma: no cover - 线程相关
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait(0.2)
                if self._stop and not self._queue:
                    return
                _prio, _order, tid, fn = self._queue.pop(0)

            def progress(payload, _t=tid):
                try:
                    self.progressed.emit(_t, payload)
                except RuntimeError:
                    # 界面（接收方）已经销毁：退出路径，忽略即可
                    pass

            try:
                result = fn(progress)
            except BaseException as exc:  # noqa: BLE001 - 兜住一切，不炸界面
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                try:
                    self.failed.emit(tid, error_text(exc) + "\n" + traceback.format_exc(limit=3))
                except RuntimeError:
                    return
            else:
                try:
                    self.completed.emit(tid, result)
                except RuntimeError:
                    return

    # ---- UI 线程回调分发 ----

    def _pop(self, tid: int):
        cb = self._callbacks.pop(tid, (None, None, None))
        self._busy = max(0, self._busy - 1)
        self.busy_changed.emit(self._busy)
        return cb

    def _on_completed(self, tid: int, result: Any) -> None:
        on_done, _on_error, _on_progress = self._pop(tid)
        if on_done is not None:
            try:
                on_done(result)
            except Exception:  # pragma: no cover - 回调自身出错不应影响后续任务
                traceback.print_exc()

    def _on_failed(self, tid: int, msg: str) -> None:
        _on_done, on_error, _on_progress = self._pop(tid)
        if on_error is not None:
            try:
                on_error(msg)
            except Exception:  # pragma: no cover
                traceback.print_exc()

    def _on_progressed(self, tid: int, payload: Any) -> None:
        on_progress = self._callbacks.get(tid, (None, None, None))[2]
        if on_progress is not None:
            try:
                on_progress(payload)
            except Exception:  # pragma: no cover
                traceback.print_exc()


# --------------------------------------------------------------------------
# 界面上下文
# --------------------------------------------------------------------------


class UiContext(QObject):
    """所有页面共享的上下文对象（由主窗口创建并注入）。"""

    cancel_operations = Signal()
    log_line = Signal(str, str)            # level, text
    selection_changed = Signal(int)        # 通道勾选掩码
    connection_changed = Signal(bool)      # 是否已连接
    caps_changed = Signal(object)          # K.Caps 或 None
    info_changed = Signal(object)          # ConnectionInfo 或 None
    mode_changed = Signal(int, int)        # chan, mode
    playing_changed = Signal(int)          # chan
    banner_requested = Signal(str, str)    # level, text

    def __init__(self, device: Optional[AWTestDevice] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.device: Optional[AWTestDevice] = device
        self.runner = TaskRunner(self)
        self.bridge = DeviceBridge(self)
        self.info: Optional[ConnectionInfo] = None
        self.caps: Optional[K.Caps] = None
        self.safety: Optional[K.SafetyCfg] = None
        self.selected_mask: int = 0
        self.last_error: str = ""
        self.heartbeat_period_s: float = C.DEFAULT_HEARTBEAT_TIMEOUT_MS / 3000.0
        if self.device is not None:
            self.bridge.attach(self.device)

    # ---- 状态查询 ----

    @property
    def connected(self) -> bool:
        return bool(self.device is not None and self.device.is_open)

    def cap(self, bit: int) -> bool:
        """能力位（未连接时按"本板期望值"返回，避免界面全灰得没法看）。"""
        if self.caps is not None:
            return bool(self.caps.caps & bit)
        return bool(C.CAPS_EXPECTED_BOARD_A & bit)

    def set_caps(self, caps: Optional[K.Caps]) -> None:
        self.caps = caps
        self.caps_changed.emit(caps)

    def set_selection(self, mask: int) -> None:
        mask &= 0xFF
        if mask != self.selected_mask:
            self.selected_mask = mask
            self.selection_changed.emit(mask)

    def selected_channels(self) -> List[int]:
        return mask_to_list(self.selected_mask)

    @property
    def channels(self) -> int:
        return self.caps.channel_count if self.caps else C.CHANNEL_COUNT

    # ---- 日志 ----

    def log(self, text: str, level: str = "info") -> None:
        if level == "error":
            self.last_error = text
        self.log_line.emit(level, text)

    def user(self, text: str) -> None:
        self.log(f"[用户操作] {text}", "user")

    # ---- 任务 ----

    def task(self, fn, on_done=None, on_error=None, on_progress=None,
             urgent: bool = False, name: str = "") -> int:
        """把一个阻塞式调用丢给工作线程。``name`` 只用于错误日志前缀。"""
        def _err(msg: str) -> None:
            head = f"{name} 失败：" if name else ""
            self.log(head + msg.splitlines()[0], "error")
            if on_error is not None:
                on_error(msg)
        return self.runner.submit(fn, on_done=on_done, on_error=_err,
                                  on_progress=on_progress, urgent=urgent)

    # ---- 对话框 ----

    def require_device(self, parent: Optional[QWidget] = None) -> bool:
        if self.connected:
            return True
        QMessageBox.warning(parent, "未连接",
                            "尚未连接设备。请先在工具栏选择串口并点击『连接』。")
        return False

    @staticmethod
    def confirm(parent: Optional[QWidget], title: str, text: str,
                detail: str = "", danger: bool = False) -> bool:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
        box.setText(text)
        if detail:
            box.setInformativeText(detail)
        yes = box.addButton("确认执行", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()
        return box.clickedButton() is yes

    @staticmethod
    def notify(parent: Optional[QWidget], title: str, text: str,
               detail: str = "") -> None:
        """信息提示框。

        注意：**不要**把它命名为 ``info`` —— 实例属性 ``self.info``
        （``ConnectionInfo``）会把同名方法遮住，导致 ``ctx.info(...)`` 变成
        "NoneType is not callable"。
        """
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Information)
        box.setText(text)
        if detail:
            box.setInformativeText(detail)
        box.exec()

    @staticmethod
    def warn(parent: Optional[QWidget], title: str, text: str,
             detail: str = "") -> None:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(text)
        if detail:
            box.setInformativeText(detail)
        box.exec()

    @staticmethod
    def error(parent: Optional[QWidget], title: str, text: str,
              detail: str = "") -> None:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Critical)
        box.setText(text)
        if detail:
            box.setInformativeText(detail)
        box.exec()

    # ---- 关闭 ----

    def shutdown(self) -> None:
        self.runner.shutdown()


# --------------------------------------------------------------------------
# 页面基类
# --------------------------------------------------------------------------


class BasePage(QWidget):
    """页面基类：统一接收上下文与连接/能力/勾选变化。"""

    title = "页面"

    def __init__(self, ctx: UiContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.build()
        ctx.connection_changed.connect(self.on_connection_changed)
        ctx.caps_changed.connect(self.on_caps_changed)
        ctx.selection_changed.connect(self.on_selection_changed)

    # ---- 子类实现 ----

    def build(self) -> None:  # pragma: no cover - 由子类覆盖
        """构造控件。**必须完全自包含**（在信号连接之前被调用）。"""

    def on_connection_changed(self, ok: bool) -> None:
        """连接状态变化。"""

    def on_caps_changed(self, caps) -> None:
        """能力位变化。"""

    def on_selection_changed(self, mask: int) -> None:
        """通道勾选变化。"""

    # ---- 便捷 ----

    def default_channel(self) -> int:
        """页面默认操作通道：优先取勾选的第一路，否则 CH0。"""
        chans = self.ctx.selected_channels()
        return chans[0] if chans else 0


__all__ = [
    "DeviceBridge", "TaskRunner", "UiContext", "BasePage",
    "HAVE_WAVEFORM", "HAVE_SCRIPT", "WAVEFORM_ERROR", "SCRIPT_ERROR",
    "missing_module_hint", "cap_reason", "error_text", "fmt_hex",
    "mask_to_list", "mask_to_text", "LOG_COLORS", "LOG_LEVELS", "LOG_LEVEL_CN",
]
