# -*- coding: utf-8 -*-
"""测试脚本执行引擎（`05` 文档 §5.9）。

顺序执行 :class:`~awtest.script.schema.TestScript` 的步骤，逐步产出
:class:`StepResult`，最后汇总成 :class:`RunResult`（可直接交给
:mod:`awtest.script.report` 导出）。

设计要点：

* **只用** ``AWTestDevice`` 的高层方法（接口约定 §1），不自己拼字节。
* 每步都走一次"检查点"：先等暂停闸门，再问 ``should_abort``；
  :meth:`ScriptEngine.abort` 置位后最迟 20 ms 内停下（远小于要求的 100 ms）。
* 任何一步抛 ``DeviceError``：若该步声明了 ``expect_error``（``true`` / 错误码数字 /
  ``"ERR_SAFETY_LIMIT"`` 名字）或断言步的 ``expect`` 写成 ``ERR_xxx``，
  就按期望比对，**命中即 pass**；否则记为 fail，并把错误码与说明文本写进报告。
* ``prompt`` 通过 ``prompt_handler(message, field, unit)`` 收集人工读数；
  没有 handler 时该步记为 ``skip`` 并在 note 里写明原因（`06` 文档 §3 的"半自动"项）。
* 事件（``EVT_PLAY_DONE`` / ``EVT_SAFETY_TRIP`` …）通过 ``device.t.on_event`` 旁路捕获，
  供 ``wait_play_done`` / ``wait_event`` 使用；设备没有事件通道时自动退化为轮询。

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import profiles as P
from ..protocol import codec as K
from ..protocol import commands as C
from ..protocol.framing import CHAN_ALL
from ..protocol.transport import DeviceError, TransportError
from ..waveform import convert
from ..waveform.generator import clip, generate, shape_code, to_bytes
from ..waveform.io_csv import load_csv
from .schema import (PROFILE_FIELDS, RATE_WORDS, STATUS_FAIL,
                     STATUS_PASS, STATUS_SKIP, Step, TestScript, is_assert_op,
                     parse_error_spec)

#: 事件轮询间隔（秒）
POLL_S = 0.02

#: 中止检查的最长间隔（秒）——必须远小于 100 ms
ABORT_CHECK_S = 0.02

#: 解析"结果字符串"里的第一个数字
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


class ScriptAborted(Exception):
    """脚本被中止（内部信号，不会冒泡到调用方）。"""


# --------------------------------------------------------------------------
# 结果数据结构（接口约定 §3）
# --------------------------------------------------------------------------


@dataclass
class StepResult:
    """单步执行结果。``status`` 取 ``pass`` / ``fail`` / ``skip``。"""

    index: int
    op: str
    chan: Optional[int]
    params: Dict[str, Any]
    expected: str = ""
    actual: str = ""
    status: str = STATUS_SKIP
    elapsed_ms: float = 0.0
    note: str = ""
    matrix_id: str = ""


@dataclass
class RunResult:
    """一次脚本运行的完整结果。"""

    script: TestScript
    started_at: str
    finished_at: str = ""
    steps: List[StepResult] = field(default_factory=list)
    manual: Dict[str, str] = field(default_factory=dict)
    status: str = "running"          # running / pass / fail / abort
    log: List[str] = field(default_factory=list)
    log_file: str = ""
    version: str = ""
    port: str = ""

    # ---- 统计（报告与界面都用） ----

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.steps if s.status == STATUS_PASS)

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s.status == STATUS_FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.steps if s.status == STATUS_SKIP)

    @property
    def elapsed_ms(self) -> float:
        return sum(s.elapsed_ms for s in self.steps)

    def matrix_ids(self) -> List[str]:
        return list(self.script.matrix_ids)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------


class ScriptEngine:
    """脚本执行引擎。"""

    def __init__(self, device,
                 script: TestScript,
                 on_step: Optional[Callable[[StepResult], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 prompt_handler: Optional[Callable[[str, str, str], Optional[str]]] = None,
                 should_abort: Optional[Callable[[], bool]] = None,
                 pause_gate: Optional[Callable[[], None]] = None,
                 event_timeout_ms: int = 5000) -> None:
        self.device = device
        self.script = script
        self.on_step = on_step
        self.on_log = on_log
        self.prompt_handler = prompt_handler
        self.should_abort = should_abort
        #: 暂停闸门：每步开始时调用，界面可以在这里阻塞到用户点"继续"
        self.pause_gate = pause_gate
        self.event_timeout_ms = int(event_timeout_ms)

        #: 最近一次运行的结果（注意：属性名不能叫 run，否则会遮住 run() 方法）
        self.result: Optional[RunResult] = None
        self._aborted = False
        self._paused = False
        self._seq = 0                      # 执行序号（重复块里的步骤也连续编号）
        self._events: List[Any] = []
        self._event_cursor = 0
        self._has_events = False
        self._hooked = False
        self._wave_open: Dict[int, bool] = {}
        self._last_actual: str = ""
        self._lock = threading.RLock()

    # ==================================================================
    # 控制
    # ==================================================================

    def abort(self) -> None:
        """请求中止：每个检查点（最长 20 ms 一次）都会立刻停下。"""
        self._aborted = True
        with self._lock:
            self._paused = False           # 中止时自动解除暂停，避免卡住

    def pause(self) -> None:
        """请求暂停：下一个检查点开始阻塞。"""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """继续执行。"""
        with self._lock:
            self._paused = False

    @property
    def aborted(self) -> bool:
        return self._aborted

    # ==================================================================
    # 主流程
    # ==================================================================

    def run_script(self) -> RunResult:
        """执行脚本并返回结果（与 :meth:`run` 等价，名字更明确）。"""
        return self.run()

    def run(self) -> RunResult:
        """顺序执行全部步骤，返回 :class:`RunResult`。"""
        self._hook_events()
        result = RunResult(script=self.script, started_at=_now_text())
        result.port = self._device_port()
        result.version = str(getattr(self.device, "version", "") or "")
        self.result = result
        self._log(result, f"开始执行脚本「{self.script.name}」"
                          f"（矩阵 {','.join(self.script.matrix_ids) or '未标注'}）")
        try:
            self._exec_steps(self.script.steps, result, label="")
            result.status = STATUS_FAIL if result.failed else "pass"
        except ScriptAborted:
            result.status = "abort"
            self._log(result, "脚本被中止")
        except Exception as exc:                        # 引擎自身异常也要留痕
            result.status = STATUS_FAIL
            self._log(result, f"脚本执行中断：{type(exc).__name__}: {exc}")
        finally:
            result.finished_at = _now_text()
            self._log(result, f"脚本结束：{result.status}"
                              f"（通过 {result.passed} / 失败 {result.failed} / "
                              f"跳过 {result.skipped}，共 {result.total} 步）")
        return result

    # ---- 内部：步骤遍历 ----

    def _exec_steps(self, steps: Sequence[Step], result: RunResult, label: str) -> None:
        for step in steps:
            self._checkpoint()
            if step.op in ("repeat", "loop"):
                self._exec_repeat(step, result, label)
                continue
            step_result = self._run_step(step, result, label)
            result.steps.append(step_result)
            if self.on_step is not None:
                try:
                    self.on_step(step_result)
                except Exception:                       # UI 回调异常不该中断脚本
                    pass

    def _exec_repeat(self, step: Step, result: RunResult, label: str) -> None:
        count = self._int(step.params.get("count"), 1, minimum=1)
        raw_steps = step.params.get("steps") or []
        sub_steps: List[Step] = []
        for pos, raw in enumerate(raw_steps, 1):
            if isinstance(raw, Step):
                sub_steps.append(raw)
            elif isinstance(raw, dict):
                sub_steps.append(Step(op=str(raw.get("op", "")),
                                      params={k: v for k, v in raw.items() if k != "op"},
                                      index=pos, raw=dict(raw)))
        if not sub_steps:
            res = StepResult(index=self._next_index(), op=step.op, chan=None,
                             params=dict(step.params), status=STATUS_FAIL,
                             note="repeat/loop 的 steps 为空，没有任何可执行的步骤",
                             matrix_id=self._matrix_id(step))
            result.steps.append(res)
            if self.on_step:
                self.on_step(res)
            return
        for round_index in range(1, count + 1):
            self._checkpoint()
            round_label = f"第 {round_index}/{count} 次重复"
            if label:
                round_label = f"{label} · {round_label}"
            self._log(result, f"循环 {round_label}（{len(sub_steps)} 步）")
            self._exec_steps(sub_steps, result, round_label)

    def _run_step(self, step: Step, result: RunResult, label: str) -> StepResult:
        chan = self._chan(step.params)
        res = StepResult(index=self._next_index(), op=step.op, chan=chan,
                         params=dict(step.params), matrix_id=self._matrix_id(step))
        started = time.perf_counter()
        try:
            self._dispatch(step, res, result)
        except ScriptAborted:
            res.elapsed_ms = (time.perf_counter() - started) * 1000.0
            raise
        except DeviceError as exc:
            self._handle_device_error(step, res, exc)
        except TransportError as exc:
            res.status = STATUS_FAIL
            res.actual = res.actual or "传输层错误"
            res.note = f"传输层错误：{exc}"
        except (ValueError, TypeError, KeyError) as exc:
            res.status = STATUS_FAIL
            res.note = f"参数或数据错误：{type(exc).__name__}: {exc}"
        except Exception as exc:                        # 兜底：不让一步拖垮整份报告
            res.status = STATUS_FAIL
            res.note = f"步骤执行异常：{type(exc).__name__}: {exc}"
        res.elapsed_ms = (time.perf_counter() - started) * 1000.0
        if label and step.op not in ("log",):
            res.note = (res.note + "；" if res.note else "") + label
        if res.status == STATUS_PASS and not res.actual:
            res.actual = "完成"
        self._last_actual = res.actual
        if res.status == STATUS_FAIL:
            self._log(result, f"第 {res.index} 步 {res.op} 判定失败：{res.note or res.actual}")
        return res

    # ---- 中止 / 暂停检查点 ----

    def _should_stop(self) -> bool:
        if self._aborted:
            return True
        if self.should_abort is not None:
            try:
                return bool(self.should_abort())
            except Exception:
                return False
        return False

    def _checkpoint(self) -> None:
        """暂停闸门 + 中止检查；中止响应时间 ≤ 20 ms。"""
        while True:
            with self._lock:
                paused = self._paused
            if not paused or self._should_stop():
                break
            time.sleep(ABORT_CHECK_S)
        if self._should_stop():
            raise ScriptAborted()
        if self.pause_gate is not None:
            self.pause_gate()
        if self._should_stop():
            raise ScriptAborted()

    def _next_index(self) -> int:
        self._seq += 1
        return self._seq

    def _matrix_id(self, step: Step) -> str:
        value = step.params.get("matrix_id")
        if value:
            return str(value)
        return self.script.matrix_id

    # ---- 小工具 ----

    @staticmethod
    def _int(value: Any, default: Optional[int] = None,
             minimum: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            out = value
        elif isinstance(value, float):
            out = int(round(value))
        elif isinstance(value, str):
            text = value.strip()
            try:
                out = int(text, 0)
            except ValueError:
                try:
                    out = int(text, 16)
                except ValueError:
                    return default
        else:
            return default
        if minimum is not None and out < minimum:
            return minimum
        return out

    @classmethod
    def _num(cls, value: Any, default: Optional[float] = None) -> Optional[float]:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = _NUM_RE.search(value)
            if match:
                return float(match.group(0))
        return default

    @staticmethod
    def _flag(params: Dict[str, Any], key: str, default: bool = False) -> bool:
        if key not in params or params[key] is None:
            return default
        value = params[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是", "开")

    def _chan(self, params: Dict[str, Any], key: str = "chan") -> Optional[int]:
        """取通道号：``0..7``、``0xFF`` / ``"all"``（广播）或 ``None``。"""
        if key not in params or params[key] is None:
            return None
        value = params[key]
        if isinstance(value, str):
            text = value.strip()
            if text.lower() in ("all", "全部", "广播"):
                return CHAN_ALL
            parsed = self._int(text)
            return parsed
        return self._int(value)

    def _mask(self, params: Dict[str, Any]) -> int:
        """取通道掩码：支持 ``chan_mask`` / ``mask`` 整数或通道号数组。"""
        value = params.get("chan_mask", params.get("mask"))
        if value is None:
            value = params.get("channels")
        if isinstance(value, (list, tuple)):
            mask = 0
            for ch in value:
                if isinstance(ch, str) and ch.strip().lower() in ("all", "全部"):
                    return 0xFF
                parsed = self._int(ch, 0)
                if parsed is not None and 0 <= parsed <= 7:
                    mask |= (1 << parsed)
            return mask & 0xFF
        parsed = self._int(value, 0)
        return int(parsed) & 0xFF

    @staticmethod
    def _mode_code(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError(f"未知播放模式：{value!r}")
        if isinstance(value, int):
            if 0 <= value <= 3:
                return value
            raise ValueError(f"未知播放模式编码：{value}")
        if isinstance(value, str):
            text = value.strip().upper()
            if text in C.MODE_CODES:
                return C.MODE_CODES[text]
            try:
                return int(text, 0)
            except ValueError:
                pass
        raise ValueError(f"未知播放模式：{value!r}（可用 RAM/RTP/CONT/TRIG）")

    @staticmethod
    def _rate_code(value: Any) -> int:
        if value is None:
            return C.RATE_24K
        if isinstance(value, bool):
            raise ValueError("采样率写法非法")
        if isinstance(value, int):
            if value in (0, 1, 2, 3):
                return value
            if value in (12000, 24000, 48000):
                return {12000: C.RATE_12K, 24000: C.RATE_24K, 48000: C.RATE_48K}[value]
            raise ValueError(f"未知采样率编码：{value}")
        text = str(value).strip().lower()
        if text in RATE_WORDS:
            return RATE_WORDS[text]
        try:
            return rate_code_from_hz(int(text, 0))
        except ValueError:
            pass
        raise ValueError(f"未知采样率：{value!r}")

    # ---- 事件通道 ----

    def _hook_events(self) -> None:
        """旁路监听 ``device.t.on_event``（设备没有事件通道时静默跳过）。"""
        if self._hooked:
            return
        self._hooked = True
        transport = getattr(self.device, "t", None)
        if transport is None or not hasattr(transport, "on_event"):
            return
        previous = getattr(transport, "on_event", None)

        def _capture(frame: Any) -> None:
            self._events.append(frame)
            if callable(previous):
                try:
                    previous(frame)
                except Exception:
                    pass
            self._log_event(frame)

        try:
            transport.on_event = _capture
            self._has_events = True
        except Exception:
            self._has_events = False

    def _log_event(self, frame: Any) -> None:
        if self.result is None:
            return
        try:
            event = K.decode_event(frame)
        except Exception:
            event = None
        if event is None:
            return
        text = ""
        if isinstance(event, K.EvtPlayDone):
            text = f"EVT_PLAY_DONE(reason={event.reason} " \
                   f"{C.PLAY_DONE_REASON_NAMES.get(event.reason, '?')}, " \
                   f"elapsed={event.elapsed_ms} ms)"
        elif isinstance(event, K.EvtSafetyTrip):
            text = f"EVT_SAFETY_TRIP(reason={event.reason} " \
                   f"{C.TRIP_REASON_NAMES.get(event.reason, '?')}, " \
                   f"mask=0x{event.affected_mask:02X})"
        elif isinstance(event, K.EvtError):
            text = f"EVT_ERROR({event.err_name}: {event.text})"
        elif isinstance(event, K.EvtLog):
            text = f"EVT_LOG[{C.LOG_LEVEL_NAMES.get(event.level, event.level)}] {event.text}"
        if text:
            self._append_log(text)

    def _wait_event(self, cmd: int, chan: Optional[int] = None,
                    timeout_ms: int = 3000,
                    predicate: Optional[Callable[[Any], bool]] = None) -> Optional[Any]:
        """等待某个事件帧（已被 :meth:`_hook_events` 捕获）。"""
        if not self._has_events:
            return None
        deadline = time.perf_counter() + max(0, timeout_ms) / 1000.0
        while True:
            for idx in range(self._event_cursor, len(self._events)):
                frame = self._events[idx]
                if getattr(frame, "cmd", None) != cmd:
                    continue
                frame_chan = getattr(frame, "chan", None)
                if chan is not None and frame_chan not in (chan, CHAN_ALL, 0xFE):
                    continue
                event = K.decode_event(frame)
                if event is None:
                    continue
                if predicate is not None and not predicate(event):
                    continue
                self._event_cursor = idx + 1
                return event
            if time.perf_counter() >= deadline:
                return None
            time.sleep(ABORT_CHECK_S)
            if self._should_stop():
                raise ScriptAborted()

    @staticmethod
    def _event_cmd(name: str) -> Optional[int]:
        wanted = name.strip().upper()
        for cmd, cmd_name in C.CMD_NAMES.items():
            if cmd >= 0xE0 and cmd_name.upper() == wanted:
                return cmd
        return None

    # ---- 日志 ----

    def _append_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        if self.result is not None:
            self.result.log.append(line)
        if self.on_log is not None:
            try:
                self.on_log(line)
            except Exception:
                pass

    def _log(self, result: RunResult, text: str) -> None:
        self._append_log(text)

    def _device_port(self) -> str:
        info = getattr(self.device, "info", None)
        return str(getattr(info, "port", "") or "")

    # ==================================================================
    # 期望值解析与比对
    # ==================================================================

    def _expected_error(self, step: Step) -> Optional[Any]:
        """取该步声明的"期望错误"；没有则返回 ``None``。"""
        if "expect_error" in step.params and step.params["expect_error"] is not None:
            return parse_error_spec(step.params["expect_error"])
        # 断言步允许把 expect 直接写成 ERR_xxx
        expect = step.params.get("expect")
        if is_assert_op(step.op) and isinstance(expect, str) \
                and expect.strip().upper().startswith("ERR_"):
            return parse_error_spec(expect)
        return None

    def _handle_device_error(self, step: Step, res: StepResult, exc: DeviceError) -> None:
        """按 `05` 文档 §5.9 处理设备错误应答。"""
        code = int(exc.errcode)
        name = C.err_name(code)
        text = f"{name}(0x{code:02X})"
        if exc.text:
            text += f" —— {exc.text}"
        res.actual = f"设备错误应答：{text}"
        spec = self._expected_error(step)
        if spec is True:
            res.status = STATUS_PASS
            res.expected = res.expected or "任意错误应答"
            res.note = f"按预期收到错误应答：{text}"
            return
        if isinstance(spec, list):
            res.expected = res.expected or "、".join(C.err_name(c) for c in spec)
            if code in spec:
                res.status = STATUS_PASS
                res.note = f"按预期收到错误应答：{text}"
            else:
                res.status = STATUS_FAIL
                res.note = f"错误码不符：期望 {'、'.join(C.err_name(c) for c in spec)}，" \
                           f"实际 {name}"
            return
        res.status = STATUS_FAIL
        res.expected = res.expected or "正常应答"
        res.note = f"未预期的设备错误：{text}" + (f"；{exc.text}" if exc.text else "")

    @staticmethod
    def _parse_hex(text: str) -> Optional[int]:
        """寄存器场景的十六进制解析：``"0x1C"`` / ``"1C"`` / ``"28"`` -> 值。"""
        raw = text.strip()
        if not raw:
            return None
        try:
            if raw.lower().startswith(("0x", "-0x")):
                return int(raw, 16)
            return int(raw, 16)
        except ValueError:
            return None

    def _match_value(self, actual: Any, expect: Any, tolerance: float = 0.0,
                     hex_mode: bool = False) -> bool:
        """比对期望值。``hex_mode=True`` 时字符串按十六进制解析（寄存器用）。"""
        if isinstance(expect, (list, tuple)):
            return any(self._match_value(actual, e, tolerance, hex_mode) for e in expect)
        if isinstance(expect, bool):
            return bool(actual) == expect
        if isinstance(expect, (int, float)):
            actual_num = self._as_number(actual)
            if actual_num is None:
                return False
            return abs(actual_num - float(expect)) <= abs(tolerance)
        if isinstance(expect, str):
            if hex_mode:
                want = self._parse_hex(expect)
                if want is not None:
                    actual_num = self._as_number(actual)
                    return actual_num is not None and int(round(actual_num)) == want
            actual_num = self._as_number(actual)
            want_num = self._num(expect)
            if actual_num is not None and want_num is not None:
                return abs(actual_num - want_num) <= abs(tolerance)
            return str(actual).strip() == expect.strip()
        return False

    def _as_number(self, value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        return self._num(value)

    def _resolve_manual(self, value: Any, run: RunResult) -> Any:
        """解析 ``$字段`` / ``{"manual": "字段"}`` 这类引用。"""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("$"):
                return run.manual.get(text[1:])
            return value
        if isinstance(value, dict):
            for key in ("manual", "field"):
                if key in value:
                    return run.manual.get(str(value[key]))
        return value

    def _resolve_value(self, params: Dict[str, Any], run: RunResult) -> Tuple[Any, str]:
        """取出被测值，返回 ``(值, 来源说明)``。

        优先级：``previous``/``use_previous``（上一步实测值）→ ``value`` / ``actual``
        → ``field`` / ``manual``（``run.manual`` 里的人工读数）。
        """
        if self._flag(params, "previous") or self._flag(params, "use_previous"):
            return self._last_actual or None, "上一步实测值"
        for key in ("value", "actual"):
            if key in params and params[key] is not None:
                if isinstance(params[key], str) and params[key].strip() == "$last":
                    return self._last_actual or None, "上一步实测值"
                return self._resolve_manual(params[key], run), key
        for key in ("field", "manual"):
            if key in params and params[key] is not None:
                name = str(params[key])
                if name in run.manual:
                    return run.manual[name], f"人工读数 {name}"
                return None, f"人工读数 {name}（缺失）"
        if self._last_actual:
            return self._last_actual, "上一步实测值"
        return None, "（无可用的实测值）"

    # ==================================================================
    # 步骤分发
    # ==================================================================

    def _dispatch(self, step: Step, res: StepResult, run: RunResult) -> None:
        op = step.op
        p = step.params
        dev = self.device

        # ---------------- 主机侧 ----------------

        if op == "wait":
            ms = float(self._num(p.get("ms"), 0.0) or 0.0)
            self._sleep_ms(ms)
            res.expected = f"等待 {ms:.0f} ms"
            res.actual = f"已等待 {ms:.0f} ms"
            res.status = STATUS_PASS
            return

        if op == "log":
            text = str(p.get("text", ""))
            self._append_log(text)
            res.expected = "写日志"
            res.actual = text
            res.status = STATUS_PASS
            return

        if op == "prompt":
            self._op_prompt(step, res, run)
            return

        # ---------------- 断言 ----------------

        if op == "assert_read":
            self._op_assert_read(step, res)
            return
        if op == "assert_equals":
            self._op_assert_equals(step, res, run)
            return
        if op == "assert_range":
            self._op_assert_range(step, res, run)
            return

        # ---------------- 循环 ----------------

        if op in ("loop", "repeat"):
            res.expected = f"重复 {self._int(p.get('count'), 1, minimum=1)} 次"
            res.actual = "循环体已展开执行"
            res.status = STATUS_PASS
            return

        # ---------------- 记录 / 测量 ----------------

        if op in ("record", "snapshot"):
            self._op_record(step, res)
            return

        if op == "f0":
            self._op_f0(step, res)
            return

        if op == "det_read":
            self._op_det_read(step, res)
            return

        if op == "wait_play_done":
            self._op_wait_play_done(step, res)
            return

        if op == "wait_event":
            self._op_wait_event(step, res)
            return

        # ---------------- 模式 / 参数 ----------------

        if op == "set_mode":
            chan = self._require_chan(step)
            mode = self._mode_code(p.get("mode"))
            dev.set_mode(chan, mode)
            res.expected = f"切换模式 -> {C.MODE_NAMES.get(mode, mode)}"
            res.actual = f"CH{chan} 已切到 {C.MODE_NAMES.get(mode, mode)}"
            res.status = STATUS_PASS
            return

        if op == "set_profile":
            self._op_set_profile(step, res)
            return

        if op == "get_profile":
            chan = self._require_chan(step)
            prof = dev.get_profile(chan)
            res.expected = "读回 Profile"
            res.actual = f"模式={C.mode_name(prof.mode)} GAIN={prof.gain} " \
                         f"F_PRE={prof.f_pre} DRV1_LVL={prof.drv1_lvl}"
            res.note = _profile_note(prof)
            res.status = STATUS_PASS
            return

        if op == "set_default_profile":
            chan = self._require_chan(step)
            dev.set_default_profile(chan)
            res.expected = "恢复数据手册默认值"
            res.actual = f"CH{chan} 已恢复默认 Profile"
            res.status = STATUS_PASS
            return

        if op in ("set_gain", "set_vbat", "set_cont_live", "set_ram_params",
                  "set_cont_params", "set_rtp_params", "set_trig_params"):
            self._op_set_params(op, step, res)
            return

        if op == "rtp_data":
            self._op_rtp_data(step, res)
            return

        # ---------------- 寄存器 ----------------

        if op == "reg_read":
            chan = self._require_chan(step)
            addr = self._require_int(step, "addr")
            length = self._int(p.get("len"), 1, minimum=1) or 1
            data = dev.reg_read(chan, addr, length)
            value = int.from_bytes(bytes(data), "little")
            res.expected = f"读 0x{addr:02X}"
            res.actual = f"0x{value:0{2 * max(1, len(data))}X}（{len(data)} 字节）"
            res.note = f"CH{chan} 地址 0x{addr:02X} 长度 {length}"
            res.status = STATUS_PASS if data else STATUS_FAIL
            return

        if op == "reg_write":
            chan = self._require_chan(step)
            addr = self._require_int(step, "addr")
            if "data" in p and isinstance(p["data"], (list, tuple)):
                data = bytes(int(v) & 0xFF for v in p["data"])
            else:
                data = bytes([self._require_int(step, "value") & 0xFF])
            dev.reg_write(chan, addr, data)
            res.expected = f"写 0x{addr:02X}"
            res.actual = "0x" + data.hex().upper()
            res.status = STATUS_PASS
            return

        if op == "reg_update":
            chan = self._require_chan(step)
            addr = self._require_int(step, "addr")
            mask = self._require_int(step, "mask")
            value = self._require_int(step, "value")
            resp = dev.reg_update(chan, addr, mask, value)
            res.expected = f"0x{addr:02X} 的 mask=0x{mask:02X} 位改为 0x{value & mask:02X}"
            res.actual = f"old=0x{resp.old:02X} new=0x{resp.new:02X}"
            res.status = STATUS_PASS
            return

        # ---------------- 播放 ----------------

        if op == "play":
            chan = self._require_chan(step)
            timeout_ms = self._int(p.get("timeout_ms"), 0) or 0
            override = self._flag(p, "override")
            resp = dev.play(chan, timeout_ms, override)
            res.expected = f"CH{chan} 开始播放（超时 {timeout_ms or '默认'} ms）"
            res.actual = f"已启动，实际安全超时 {resp.actual_timeout_ms} ms"
            if override:
                res.note = "使用了 override（绕过同时满幅路数限制，危险操作）"
            res.status = STATUS_PASS
            return

        if op == "play_multi":
            mask = self._mask(p)
            timeout_ms = self._int(p.get("timeout_ms"), 0) or 0
            skew_us = self._int(p.get("skew_us"), 0) or 0
            resp = dev.play_multi(mask, timeout_ms, skew_us, self._flag(p, "override"))
            res.expected = f"掩码 0x{mask:02X} 多路启动"
            res.actual = f"started_mask=0x{resp.started_mask:02X} skew={resp.actual_skew_us} µs"
            res.status = STATUS_PASS
            return

        if op == "stop":
            chan = self._require_chan(step)
            immediate = self._flag(p, "immediate", True)
            dev.stop(chan, immediate)
            res.expected = f"CH{chan} 停止（{'立即' if immediate else '播完'}）"
            res.actual = "已发送停止"
            res.status = STATUS_PASS
            return

        if op == "stop_all":
            immediate = self._flag(p, "immediate", True)
            mask = dev.stop_all(immediate)
            res.expected = "全部通道停止"
            res.actual = f"stopped_mask=0x{int(mask) & 0xFF:02X}"
            res.status = STATUS_PASS
            return

        if op == "trig_park":
            dev.trig_park()
            res.expected = "所有 TRIG 输出置低"
            res.actual = "已静默"
            res.status = STATUS_PASS
            return

        if op == "get_play_state":
            chan = self._require_chan(step)
            state = dev.get_play_state(chan)
            res.expected = "读取播放状态"
            res.actual = f"playing={state.playing} GLB_STATE=" \
                         f"{C.glb_state_name(state.glb_state)} elapsed={state.elapsed_ms} ms"
            res.status = STATUS_PASS
            return

        if op == "trig_pulse":
            mask = self._mask(p)
            started = dev.trig_pulse(mask,
                                     self._int(p.get("width_us"), 20) or 20,
                                     self._int(p.get("count"), 1) or 1,
                                     self._int(p.get("period_ms"), 100) or 0,
                                     self._int(p.get("initial_delay_us"), 0) or 0)
            res.expected = f"掩码 0x{mask:02X} 发送脉冲串"
            res.actual = f"started_mask=0x{int(started) & 0xFF:02X}"
            res.status = STATUS_PASS
            return

        if op == "trig_pattern":
            mask = self._mask(p)
            dev.trig_pattern(mask,
                             self._int(p.get("on_us"), 20) or 20,
                             self._int(p.get("off_us"), 20) or 0,
                             self._int(p.get("count"), 10) or 1,
                             self._int(p.get("period_ms"), 100) or 0)
            res.expected = f"掩码 0x{mask:02X} 本地脉冲串"
            res.actual = f"on={self._int(p.get('on_us'), 20)} µs " \
                         f"off={self._int(p.get('off_us'), 20)} µs " \
                         f"count={self._int(p.get('count'), 10)}"
            res.status = STATUS_PASS
            return

        if op == "trig_level":
            mask = self._mask(p)
            dev.trig_level(mask, int(self._int(p.get("level"), 1) or 0),
                           self._int(p.get("hold_ms"), 1) or 1)
            res.expected = f"掩码 0x{mask:02X} 电平触发"
            res.actual = f"level={p.get('level', 1)} hold={p.get('hold_ms', 1)} ms"
            res.status = STATUS_PASS
            return

        # ---------------- 波形 ----------------

        if op == "wave_begin":
            self._op_wave_begin(step, res)
            return
        if op == "wave_add":
            self._op_wave_add(step, res)
            return
        if op == "wave_commit":
            self._op_wave_commit(step, res)
            return
        if op == "wave_abort":
            chan = self._require_chan(step)
            self.device.wave_abort(chan)
            self._wave_open[chan] = False
            res.expected = "放弃波形会话"
            res.actual = "已放弃"
            res.status = STATUS_PASS
            return
        if op == "wave_erase":
            chan = self._require_chan(step)
            self.device.wave_erase(chan)
            res.expected = "清空波形库"
            res.actual = "已清空"
            res.status = STATUS_PASS
            return
        if op == "wave_gen":
            self._op_wave_gen(step, res)
            return
        if op == "wave_queue":
            chan = self._require_chan(step)
            ids = [self._int(v, 0) or 0 for v in (p.get("ids") or [])]
            self.device.wave_queue(chan, ids)
            res.expected = f"播放队列 {ids}"
            res.actual = "已下发"
            res.status = STATUS_PASS
            return
        if op == "wave_get_library":
            chan = self._require_chan(step)
            lib = self.device.wave_get_library(chan)
            res.expected = "读取固件侧波形库地址表"
            res.actual = f"版本 {lib.lib_version}，{lib.wave_count} 条，" \
                         f"共 {lib.total_bytes} 字节，base=0x{lib.base_addr:04X}"
            res.note = "；".join(f"id{e.wave_id}:{e.start}-{e.end}" for e in lib.entries)
            res.status = STATUS_PASS
            return
        if op == "wave_readback":
            chan = self._require_chan(step)
            wave_id = self._require_int(step, "wave_id")
            offset = self._int(p.get("offset"), 0) or 0
            length = self._int(p.get("len"), 16, minimum=1) or 16
            rb = self.device.wave_readback(chan, wave_id, offset, length)
            res.expected = f"回读波形 {wave_id} @{offset}"
            res.actual = f"{len(rb.samples)} 字节：" + rb.samples.hex().upper()[:64]
            res.status = STATUS_PASS
            return

        # ---------------- 扫描 / 测试 ----------------

        if op == "sweep_amp":
            self._op_sweep_amp(step, res, run)
            return
        if op == "sweep_freq":
            chan = self._require_chan(step)
            resp = self.device.sweep_freq(chan,
                                          self._require_int(step, "from_hz"),
                                          self._require_int(step, "to_hz"),
                                          self._require_int(step, "step_hz"),
                                          self._int(p.get("dwell_ms"), 200) or 200)
            res.expected = "频率扫描已启动"
            res.actual = f"accepted={resp.accepted} 共 {resp.total_steps} 步"
            res.status = STATUS_PASS if resp.accepted else STATUS_FAIL
            return
        if op == "sweep_abort":
            self.device.sweep_abort()
            res.expected = "中止扫描"
            res.actual = "已发送"
            res.status = STATUS_PASS
            return
        if op == "test_run":
            test_id = self._require_int(step, "test_id")
            ok = self.device.test_run(test_id)
            res.expected = f"运行内置测试 {test_id}"
            res.actual = "已接受" if ok else "被拒绝"
            res.status = STATUS_PASS if ok else STATUS_FAIL
            return
        if op == "test_abort":
            self.device.test_abort()
            res.expected = "中止内置测试"
            res.actual = "已发送"
            res.status = STATUS_PASS
            return
        if op == "selftest":
            ok = self.device.selftest()
            res.expected = "整机自检已启动"
            res.actual = "已接受" if ok else "被拒绝"
            res.status = STATUS_PASS if ok else STATUS_FAIL
            return
        if op == "mon_start":
            mask = self._mask(p)
            self.device.mon_start(mask, self._int(p.get("period_ms"), 100) or 2,
                                  self._int(p.get("fields"), 0x07) or 0)
            res.expected = f"掩码 0x{mask:02X} 开始周期上报"
            res.actual = "已下发"
            res.status = STATUS_PASS
            return
        if op == "mon_stop":
            mask = self._mask(p)
            self.device.mon_stop(mask)
            res.expected = f"掩码 0x{mask:02X} 停止周期上报"
            res.actual = "已下发"
            res.status = STATUS_PASS
            return

        # ---------------- 系统 / 总线 / 器件 ----------------

        if op == "ping":
            info = dev.ping()
            res.expected = "PING 有应答"
            res.actual = f"proto={info.proto_ver} fw={info.fw_version} " \
                         f"board={info.board_id} uptime={info.uptime_ms} ms"
            res.status = STATUS_PASS
            return

        if op == "get_caps":
            caps = dev.get_caps()
            res.expected = "读取能力位"
            res.actual = f"{caps.channel_count} 通道，caps=0x{caps.caps:08X}"
            res.note = C.caps_summary(caps.caps)
            res.status = STATUS_PASS
            return

        if op == "get_status":
            self._op_get_status(step, res)
            return

        if op == "get_safety":
            cfg = dev.get_safety()
            res.expected = "读取安全参数"
            res.actual = f"play_timeout={cfg.play_timeout_ms} ms " \
                         f"max_simul_full_amp={cfg.max_simul_full_amp} " \
                         f"heartbeat={cfg.heartbeat_timeout_ms} ms flags=0x{cfg.flags:02X}"
            res.status = STATUS_PASS
            return

        if op == "set_safety":
            cfg = K.SafetyCfg(
                play_timeout_ms=self._int(p.get("play_timeout_ms"),
                                          C.DEFAULT_PLAY_TIMEOUT_MS) or 0,
                max_simul_full_amp=self._int(p.get("max_simul_full_amp"),
                                             C.DEFAULT_MAX_SIMUL_FULL_AMP) or 0,
                heartbeat_timeout_ms=self._int(p.get("heartbeat_timeout_ms"),
                                               C.DEFAULT_HEARTBEAT_TIMEOUT_MS) or 0,
                flags=self._int(p.get("flags"), 0) or 0)
            back = dev.set_safety(cfg)
            res.expected = "写入安全参数"
            res.actual = f"回读：max_simul_full_amp={back.max_simul_full_amp} " \
                         f"play_timeout={back.play_timeout_ms} ms"
            res.status = STATUS_PASS
            return

        if op == "clear_errors":
            dev.clear_errors()
            res.expected = "清除错误与安全锁定"
            res.actual = "已发送"
            res.status = STATUS_PASS
            return

        if op == "bus_probe":
            sel = self._int(p.get("bus_sel"), 0) or 0
            info = dev.bus_probe(sel)
            res.expected = "总线探测成功"
            res.actual = f"{info.bus_name}(IO{info.sda_gpio}/IO{info.scl_gpio})@" \
                         f"{info.speed_hz // 1000}kHz tca={'在线' if info.tca_present else '离线'}"
            res.status = STATUS_PASS
            return

        if op == "bus_set_speed":
            hz = self._int(p.get("speed_hz", p.get("hz")), 400000) or 400000
            actual = dev.bus_set_speed(hz)
            res.expected = f"总线速率 -> {hz} Hz"
            res.actual = f"实际 {actual} Hz"
            res.status = STATUS_PASS if actual == hz else STATUS_FAIL
            return

        if op == "bus_set_pullup":
            dev.bus_set_pullup(self._flag(p, "enable", True))
            res.expected = "设置内部上拉"
            res.actual = f"enable={self._flag(p, 'enable', True)}"
            res.status = STATUS_PASS
            return

        if op == "bus_recover":
            method = dev.bus_recover()
            res.expected = "总线恢复"
            res.actual = C.BUS_RECOVER_METHODS.get(int(method), f"方法 {method}")
            res.status = STATUS_PASS if int(method) else STATUS_FAIL
            return

        if op == "tca_reset":
            dev.tca_reset(self._int(p.get("hold_us"), 10) or 10)
            res.expected = "TCA9548A 复位脉冲"
            res.actual = "已发送"
            res.status = STATUS_PASS
            return

        if op == "tca_set_mask":
            applied = dev.tca_set_mask(self._int(p.get("mask"), 0) or 0)
            res.expected = f"选通掩码 0x{self._int(p.get('mask'), 0):02X}"
            res.actual = f"applied=0x{int(applied) & 0xFF:02X}"
            res.status = STATUS_PASS
            return

        if op == "tca_get_mask":
            mask = dev.tca_get_mask()
            res.expected = "读取 TCA 选通掩码"
            res.actual = f"0x{int(mask) & 0xFF:02X}"
            res.status = STATUS_PASS
            return

        if op == "ch_probe":
            self._op_ch_probe(step, res)
            return

        if op == "ch_probe_all":
            self._op_ch_probe_all(step, res)
            return

        if op == "ch_get_info":
            chan = self._require_chan(step)
            info = dev.ch_get_info(chan)
            chipid = (info.chipid_h << 8) | info.chipid_l
            res.expected = "读取通道信息"
            res.actual = f"CH{chan} present={info.present} CHIPID=0x{chipid:04X} " \
                         f"模式={C.mode_name(info.mode)} playing={info.playing} " \
                         f"GLB_STATE={C.glb_state_name(info.glb_state)}"
            res.status = STATUS_PASS
            return

        if op == "aw_softrst":
            chan = self._require_chan(step)
            dev.aw_softrst(chan, replay_profile=True)
            res.expected = f"CH{chan} 软复位并重放 Profile"
            res.actual = "已完成"
            res.status = STATUS_PASS
            return

        if op == "aw_wake":
            chan = self._require_chan(step)
            dev.aw_wake(chan)
            res.expected = f"CH{chan} 唤醒"
            res.actual = "已下发"
            res.status = STATUS_PASS
            return

        if op == "aw_standby":
            chan = self._require_chan(step)
            dev.aw_standby(chan)
            res.expected = f"CH{chan} 进入待机"
            res.actual = "已下发"
            res.status = STATUS_PASS
            return

        if op == "aw_init_default":
            chan = self._require_chan(step)
            resp = dev.aw_init_default(chan)
            res.expected = "执行标准上电初始化序列"
            res.actual = f"{resp.steps_ok}/{resp.steps_total} 步成功"
            res.status = STATUS_PASS if resp.steps_ok == resp.steps_total else STATUS_FAIL
            return

        if op == "aw_get_state":
            chan = self._require_chan(step)
            state = dev.aw_get_state(chan)
            res.expected = "读取器件状态"
            res.actual = f"GLB_STATE={C.glb_state_name(state.glb_state)} " \
                         f"SYSST=0x{state.sysst:02X} SYSST2=0x{state.sysst2:02X} " \
                         f"ok={state.ok}"
            res.status = STATUS_PASS if state.ok else STATUS_FAIL
            return

        raise ValueError(f"未实现的操作：{op}")

    # ==================================================================
    # 各 op 的实现
    # ==================================================================

    def _require_chan(self, step: Step) -> int:
        chan = self._chan(step.params)
        if chan is None:
            raise ValueError(f"{step.op} 需要通道号 chan（0..7 或 \"all\"）")
        return chan

    def _require_int(self, step: Step, key: str) -> int:
        value = self._int(step.params.get(key))
        if value is None:
            raise ValueError(f"{step.op} 缺少必填参数 {key}")
        return value

    def _sleep_ms(self, ms: float) -> None:
        """可中断的等待（中止响应 ≤ 20 ms）。"""
        deadline = time.perf_counter() + max(0.0, ms) / 1000.0
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(ABORT_CHECK_S, remaining))
            if self._should_stop():
                raise ScriptAborted()

    # ---- 人工输入 ----

    def _op_prompt(self, step: Step, res: StepResult, run: RunResult) -> None:
        p = step.params
        message = str(p.get("message", ""))
        field_name = str(p.get("field", "")).strip() or "manual"
        unit = str(p.get("unit", "") or "")
        res.expected = message or f"人工录入 {field_name}"
        if self.prompt_handler is None:
            res.status = STATUS_SKIP
            res.actual = "（未采集）"
            res.note = "没有可用的输入界面（prompt_handler 未提供），该人工读数被跳过"
            return
        value = self.prompt_handler(message, field_name, unit)
        if value is None or str(value).strip() == "":
            res.status = STATUS_SKIP
            res.actual = "（用户未填写）"
            res.note = f"人工读数「{field_name}」未填写，已跳过"
            return
        text = str(value).strip()
        run.manual[field_name] = text
        res.actual = f"{text} {unit}".strip()
        res.status = STATUS_PASS
        res.note = f"人工读数已记入报告的「关键实测值」：{field_name}"

    # ---- 断言 ----

    def _op_assert_read(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        addr = self._require_int(step, "addr")
        length = self._int(p.get("len"), 1, minimum=1) or 1
        expect = p.get("expect")
        mask = self._int(p.get("mask"))
        tolerance = self._num(p.get("tolerance"), 0.0) or 0.0
        data = self.device.reg_read(chan, addr, length)
        value = int.from_bytes(bytes(data), "little")
        compared = value & mask if mask is not None else value
        res.expected = f"0x{addr:02X} == {expect}" + (f"（mask 0x{mask:02X}）" if mask else "")
        res.actual = f"0x{compared:0{2 * max(1, length)}X}"
        res.note = f"CH{chan} 地址 0x{addr:02X}，原始读回 0x{value:0{2 * max(1, length)}X}"
        if is_assert_op(step.op) and isinstance(expect, str) \
                and expect.strip().upper().startswith("ERR_"):
            res.status = STATUS_FAIL
            res.note += "；本步把 expect 写成了错误码，但读寄存器并未报错"
            return
        res.status = STATUS_PASS if self._match_value(compared, expect, tolerance,
                                                      hex_mode=True) else STATUS_FAIL
        if res.status == STATUS_FAIL:
            res.note += "；读回值与期望不符"

    def _op_assert_equals(self, step: Step, res: StepResult, run: RunResult) -> None:
        p = step.params
        expect = p.get("expect")
        value, source = self._resolve_value(p, run)
        tolerance = self._num(p.get("tolerance"), 0.0) or 0.0
        res.expected = f"{expect}"
        if value is None:
            res.status = STATUS_SKIP
            res.actual = "（没有可用的实测值）"
            res.note = f"无法取得被测值（来源：{source}）"
            return
        res.actual = f"{value}"
        res.status = STATUS_PASS if self._match_value(value, expect, tolerance) else STATUS_FAIL
        res.note = f"被测值来源：{source}"
        if res.status == STATUS_FAIL:
            res.note += "；与期望不符"

    def _op_assert_range(self, step: Step, res: StepResult, run: RunResult) -> None:
        p = step.params
        value, source = self._resolve_value(p, run)
        lo = self._num(p.get("min"))
        hi = self._num(p.get("max"))
        unit = str(p.get("unit", "") or "")
        text = []
        if lo is not None:
            text.append(f">= {lo:g}")
        if hi is not None:
            text.append(f"<= {hi:g}")
        res.expected = " ".join(text) + (f" {unit}" if unit else "")
        if value is None:
            res.status = STATUS_SKIP
            res.actual = "（没有可用的实测值）"
            res.note = f"无法取得被测值（来源：{source}）"
            return
        number = self._as_number(value)
        res.actual = f"{value}" + (f" {unit}" if unit else "")
        if number is None:
            res.status = STATUS_FAIL
            res.note = f"被测值「{value}」不是数字（来源：{source}）"
            return
        ok = True
        if lo is not None and number < lo - 1e-9:
            ok = False
        if hi is not None and number > hi + 1e-9:
            ok = False
        res.status = STATUS_PASS if ok else STATUS_FAIL
        res.note = f"被测值来源：{source}"
        if not ok:
            res.note += f"；{number:g} 超出区间"

    # ---- 记录 / 测量 ----

    def _op_record(self, step: Step, res: StepResult) -> None:
        chan = self._require_chan(step)
        snap = self.device.snapshot(chan)
        text = _snapshot_text(snap, getattr(self.device, "d2s_gain", 8))
        res.expected = f"抓取 CH{chan} 快照"
        res.actual = text
        res.note = text
        if self._flag(step.params, "expect_standby") and snap.is_playing:
            res.status = STATUS_FAIL
            res.note += "；期望 STANDBY 但仍在播放"
            return
        res.status = STATUS_PASS

    def _op_f0(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        timeout_ms = self._int(p.get("timeout_ms"), 500) or 500
        mode = self._int(p.get("mode"), 0) or 0
        result = self.device.f0_measure(chan, timeout_ms, mode)
        expect_valid = self._flag(p, "expect_valid", True)
        f0 = result.f0_hz
        raw = result.raw
        diag = result.diagnosis()
        res.expected = f"F0 {'有效' if expect_valid else '无效'}（模式 {mode}）"
        valid = bool(result.valid)
        if valid and f0 is not None:
            res.actual = f"F0 = {f0:.1f} Hz（原始 0x{result.raw_h:02X}{result.raw_l:02X}）"
        else:
            res.actual = "无效（valid=0，无有效读数）"
            res.note = diag
        lo = self._num(p.get("min_hz"))
        hi = self._num(p.get("max_hz"))
        if valid and f0 is not None and lo is not None and f0 < lo:
            res.status = STATUS_FAIL
            res.note = (res.note + "；" if res.note else "") + f"F0 低于下限 {lo:g} Hz"
            return
        if valid and f0 is not None and hi is not None and f0 > hi:
            res.status = STATUS_FAIL
            res.note = (res.note + "；" if res.note else "") + f"F0 高于上限 {hi:g} Hz"
            return
        if valid and f0 is not None and raw == 0:
            res.status = STATUS_FAIL
            res.note = (res.note + "；" if res.note else "") + "原始值为 0（除零，必为无效）"
            return
        res.status = STATUS_PASS if valid == expect_valid else STATUS_FAIL
        if res.status == STATUS_FAIL and valid:
            res.note = (res.note + "；" if res.note else "") + diag

    def _op_det_read(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        seq = self._int(p.get("seq"), C.DET_SEQ_VBAT)
        result = self.device.det_read(chan, int(seq))
        d2s = self._int(p.get("d2s_gain"), getattr(self.device, "d2s_gain", 8)) or 8
        unit = {0: "V", 3: "Ω", 4: ""}.get(int(seq), "")
        value = result.value(d2s) if result.valid else None
        if int(seq) == 0:
            converted = convert.avg_to_vdd(result.avg_data)
        elif int(seq) == 3:
            converted = convert.avg_to_rl(result.avg_data, d2s)
        else:
            converted = float(result.adc_data)
        res.expected = C.DET_SEQ_NAMES.get(int(seq), f"seq={seq}")
        if value is None:
            res.actual = "无效（valid=0）"
            res.note = f"ADC_DATA={result.adc_data} AVG_DATA={result.avg_data} " \
                       f"value_x100=0x{result.value_x100:04X}"
            res.status = STATUS_FAIL
            return
        res.actual = f"{value:g} {unit}".strip()
        res.note = f"ADC_DATA={result.adc_data} AVG_DATA={result.avg_data} " \
                   f"换算值={converted:.3f} {unit}".strip()
        lo = self._num(p.get("min"))
        hi = self._num(p.get("max"))
        if (lo is not None and value < lo - 1e-9) or (hi is not None and value > hi + 1e-9):
            res.status = STATUS_FAIL
            res.note += f"；{value:g} 超出区间 [{lo}, {hi}]"
            return
        res.status = STATUS_PASS

    def _op_wait_play_done(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._chan(p) or 0
        timeout_ms = self._int(p.get("timeout_ms"), self.event_timeout_ms) or self.event_timeout_ms
        expect_reason = self._int(p.get("expect_reason"))
        res.expected = "收到 EVT_PLAY_DONE"
        if expect_reason is not None:
            res.expected += f"（reason={expect_reason} " \
                            f"{C.PLAY_DONE_REASON_NAMES.get(expect_reason, '?')}）"

        event = self._wait_event(C.EVT_PLAY_DONE, chan, timeout_ms) if self._has_events else None
        if event is not None:
            reason = int(event.reason)
            res.actual = f"EVT_PLAY_DONE(reason={reason} " \
                         f"{C.PLAY_DONE_REASON_NAMES.get(reason, '?')}, " \
                         f"耗时 {event.elapsed_ms} ms)"
            if expect_reason is None:
                res.status = STATUS_PASS
            elif reason == expect_reason:
                res.status = STATUS_PASS
                res.note = "播放结束原因与预期一致"
            else:
                res.status = STATUS_FAIL
                res.note = f"播放结束原因不符：期望 {expect_reason}，实际 {reason}"
            return

        # 退化：轮询播放状态
        state = self._poll_play_state(chan, timeout_ms)
        if state is None:
            res.status = STATUS_SKIP
            res.actual = "（无法判定）"
            res.note = ("没有事件通道（device.t.on_event 不可用），"
                        "也无法轮询 GET_PLAY_STATE，无法确认播放是否结束")
            return
        res.actual = f"轮询判定：播放已结束（GLB_STATE={C.glb_state_name(state.glb_state)}，" \
                     f"elapsed={state.elapsed_ms} ms）"
        if expect_reason is not None:
            res.status = STATUS_SKIP
            res.note = (f"未捕获 EVT_PLAY_DONE，无法核对结束原因"
                        f"（期望 reason={expect_reason}；该字段由协议 §10 事件上报）")
        else:
            res.status = STATUS_PASS

    def _poll_play_state(self, chan: int, timeout_ms: int):
        getter = getattr(self.device, "get_play_state", None)
        if getter is None:
            return None
        deadline = time.perf_counter() + max(0, timeout_ms) / 1000.0
        state = None
        while True:
            try:
                state = getter(chan)
            except DeviceError:
                return state
            if not getattr(state, "playing", 0):
                return state
            if time.perf_counter() >= deadline:
                return state
            time.sleep(POLL_S)
            if self._should_stop():
                raise ScriptAborted()

    def _op_wait_event(self, step: Step, res: StepResult) -> None:
        p = step.params
        name = str(p.get("event", "")).strip()
        timeout_ms = self._int(p.get("timeout_ms"), self.event_timeout_ms) or self.event_timeout_ms
        chan = self._chan(p)
        reason = self._int(p.get("reason"))
        wanted = self._event_cmd(name)
        res.expected = f"收到事件 {name}" + (f"（reason={reason}）" if reason is not None else "")
        if wanted is None:
            res.status = STATUS_FAIL
            res.actual = "（未知事件名）"
            res.note = f"事件名「{name}」不在协议 §10 的事件表里"
            return
        if not self._has_events:
            res.status = STATUS_SKIP
            res.actual = "（无事件通道）"
            res.note = "设备未提供 t.on_event 事件通道，无法等待事件"
            return
        predicate = None
        if reason is not None:
            predicate = lambda ev, r=reason: int(getattr(ev, "reason", -1)) == r
        event = self._wait_event(wanted, chan, timeout_ms, predicate)
        if event is None:
            res.status = STATUS_FAIL
            res.actual = f"{timeout_ms} ms 内未收到"
            res.note = f"超时未收到 {name}" + (f"（reason={reason}）" if reason is not None else "")
            return
        res.actual = f"已收到 {name}" + \
                     (f"（reason={getattr(event, 'reason', '')}）" if hasattr(event, "reason") else "")
        res.status = STATUS_PASS

    def _op_get_status(self, step: Step, res: StepResult) -> None:
        p = step.params
        status = self.device.get_status()
        res.expected = "读取设备状态"
        res.actual = f"uptime={status.uptime_ms} ms " \
                     f"safety_state={status.safety_state}" \
                     f"（{C.SAFETY_STATE_NAMES.get(status.safety_state, '未知')}）" \
                     f" last_err={C.err_name(status.last_err)}" \
                     f" active_mask=0x{status.active_mask:02X}" \
                     f" heartbeat_age={status.heartbeat_age_ms} ms"
        res.status = STATUS_PASS
        want_safety = self._int(p.get("expect_safety_state"))
        want_err = p.get("expect_last_err")
        want_err_code = parse_error_spec(want_err) if want_err is not None else None
        if want_safety is not None and status.safety_state != want_safety:
            res.status = STATUS_FAIL
            res.note = f"safety_state 期望 {want_safety}，实际 {status.safety_state}"
        elif want_err_code and status.last_err not in want_err_code:
            res.status = STATUS_FAIL
            res.note = f"last_err 期望 {C.err_name(want_err_code[0])}，" \
                       f"实际 {C.err_name(status.last_err)}"

    # ---- 参数下发 ----

    def _op_set_profile(self, step: Step, res: StepResult) -> None:
        chan = self._require_chan(step)
        fields = step.profile_fields()
        if not fields:
            res.status = STATUS_FAIL
            res.note = "set_profile 没有给出任何 Profile 字段"
            return
        base = None
        profiles = getattr(self.device, "profiles", None)
        if isinstance(profiles, dict):
            base = profiles.get(chan)
        prof = base.copy() if base is not None else P.Profile()
        unknown = [k for k in fields if k not in PROFILE_FIELDS]
        if unknown:
            res.status = STATUS_FAIL
            res.note = f"未知 Profile 字段：{', '.join(unknown)}"
            return
        for key, value in fields.items():
            current = getattr(prof, key, None)
            if isinstance(current, list):
                setattr(prof, key, list(value) if isinstance(value, (list, tuple)) else [value])
            else:
                setattr(prof, key, int(value))
        problems = prof.validate()
        if problems:
            res.status = STATUS_FAIL
            res.actual = "未下发"
            res.note = "Profile 校验未通过：" + "；".join(
                f"{name}：{why}" for name, why in problems)
            return
        resp = self.device.set_profile(chan, prof)
        changed = "、".join(f"{k}={fields[k]}" for k in sorted(fields))
        res.expected = f"CH{chan} 下发 Profile（{changed}）"
        res.actual = f"result={resp.result} first_bad_field={resp.first_bad_field}"
        res.status = STATUS_PASS if resp.result == 0 else STATUS_FAIL
        if resp.result != 0:
            res.note = f"固件拒绝了下发，首个非法字段偏移 {resp.first_bad_field}"

    def _op_set_params(self, op: str, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        dev = self.device
        if op == "set_gain":
            gain = self._require_int(step, "gain")
            dev.set_gain(chan, gain)
            res.expected = f"GAIN={gain}（{convert.gain_to_ratio(gain):.2f}×）"
        elif op == "set_vbat":
            mode = self._int(p.get("vbat_mode", p.get("mode")), 0) or 0
            ref = self._int(p.get("vbat_ref", p.get("ref")), 2) or 0
            dev.set_vbat(chan, mode, ref)
            res.expected = f"VBAT 模式={mode} 参考档={ref} " \
                           f"（{P.VBAT_REF_TABLE.get(ref, '?')} V）"
        elif op == "set_cont_live":
            drv1 = self._require_int(step, "drv1_lvl")
            drv2 = self._int(p.get("drv2_lvl"), drv1) or 0
            dev.set_cont_live(chan, drv1, drv2, self._flag(p, "noresp", True))
            res.expected = f"实时幅度 DRV1={drv1} DRV2={drv2}"
        elif op == "set_ram_params":
            params = K.RamParams(
                wavseq=[self._int(v, 0) or 0 for v in (p.get("wavseq") or [0] * 8)],
                mainloop=self._int(p.get("mainloop"), 0) or 0,
                waitslot=self._int(p.get("waitslot"), 0) or 0,
                seq_loop=[self._int(v, 0) or 0 for v in (p.get("seq_loop") or [0] * 4)])
            dev.set_ram_params(chan, params)
            res.expected = "下发 RAM 队列/循环参数"
        elif op == "set_cont_params":
            base = K.ContParams()
            kwargs = {f.name: (self._int(p.get(f.name), getattr(base, f.name)) or 0)
                      for f in dataclasses.fields(K.ContParams)}
            dev.set_cont_params(chan, K.ContParams(**kwargs))
            res.expected = f"下发 CONT 参数 F_PRE={kwargs.get('f_pre')} " \
                           f"(F0≈{convert.f_pre_to_f0(max(1, kwargs.get('f_pre', 1))):.1f} Hz)"
        elif op == "set_rtp_params":
            mode_value = p.get("wavdat_mode", p.get("rate", C.RATE_12K))
            params = K.RtpParams(
                wavdat_mode=self._rate_code(mode_value),
                auto_dynamic_sin=self._flag(p, "auto_dynamic_sin"),
                base_addr=self._int(p.get("base_addr"), 0x0800) or 0,
                fifo_ae=self._int(p.get("fifo_ae"), 512) or 0,
                fifo_af=self._int(p.get("fifo_af"), 1536) or 0)
            dev.set_rtp_params(chan, params)
            res.expected = f"RTP 采样率={C.RATE_NAMES.get(params.wavdat_mode)} " \
                           f"base=0x{params.base_addr:04X}"
        else:                                            # set_trig_params
            params = _trig_params_from(p)
            dev.set_trig_params(chan, params)
            res.expected = f"下发 TRIG 参数（{params.trigger_mode}，" \
                           f"TRG1_MODE={params.trig1_mode}，STOP={params.trig1_stop}）"
        res.actual = "已下发"
        res.status = STATUS_PASS

    def _op_rtp_data(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        offset = self._int(p.get("offset"), 0) or 0
        data = self._build_samples(p, rate_default=C.RATE_12K,
                                   start_index=offset)
        chunk = C.REG_MAX_LEN
        sent = 0
        while sent < len(data):
            piece = data[sent:sent + chunk]
            self.device.rtp_data(chan, offset + sent, to_bytes(piece))
            sent += len(piece)
        res.expected = f"RTP 流式送入 {len(data)} 个样本"
        res.actual = f"已送出 {sent} 样本（分 {(sent + chunk - 1) // chunk} 片，每片 ≤{chunk} 字节）"
        res.status = STATUS_PASS

    # ---- 波形 ----

    def _op_wave_begin(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        rate = self._rate_code(p.get("rate", p.get("sample_rate")))
        count = self._int(p.get("wave_count"), 1, minimum=1) or 1
        resp = self.device.wave_begin(chan, rate, count, self._flag(p, "overwrite", True))
        self._wave_open[chan] = True
        res.expected = f"开始波形会话：{C.RATE_NAMES.get(rate)} × {count} 条"
        res.actual = f"session={resp.session_id} max_total_samples={resp.max_total_samples} " \
                     f"sram_free={resp.sram_free}"
        res.status = STATUS_PASS

    def _op_wave_gen(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        wave_id = self._require_int(step, "wave_id")
        shape = shape_code(p.get("shape"))
        freq = self._require_int(step, "freq_hz")
        amp = self._require_int(step, "amplitude")
        cycles = self._require_int(step, "cycles")
        rate = self._rate_code(p.get("rate", p.get("sample_rate")))
        resp = self.device.wave_gen(chan, wave_id, shape, freq, amp, cycles, rate)
        res.expected = f"固件合成波形 {wave_id}：{C.SHAPE_NAMES.get(shape)} " \
                       f"{freq} Hz 幅度 {amp} × {cycles} 周期（{C.RATE_NAMES.get(rate)}）"
        res.actual = f"已生成 {resp.samples_generated} 个样本"
        res.status = STATUS_PASS

    def _op_wave_add(self, step: Step, res: StepResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        wave_id = self._require_int(step, "wave_id")
        rate = self._rate_code(p.get("rate", p.get("sample_rate")))
        samples = self._build_samples(p, rate_default=rate)
        if not self._wave_open.get(chan):
            count = self._int(p.get("wave_count"), 1, minimum=1) or 1
            self.device.wave_begin(chan, rate, count, self._flag(p, "overwrite", True))
            self._wave_open[chan] = True
        total = len(samples)
        chunk = C.WAVE_MAX_CHUNK_SAMPLES
        sent = 0
        while sent < total:
            piece = samples[sent:sent + chunk]
            last = (sent + len(piece)) >= total
            self.device.wave_add(chan, wave_id, sent, total, to_bytes(piece), last)
            sent += len(piece)
        res.expected = f"下发波形 {wave_id}：{total} 个样本"
        res.actual = f"已分 {(total + chunk - 1) // chunk} 片下发（每片 ≤{chunk} 样本）"
        res.status = STATUS_PASS

    def _op_wave_commit(self, step: Step, res: StepResult) -> None:
        chan = self._require_chan(step)
        lib_version = self._int(step.params.get("lib_version"), 1) or 1
        resp = self.device.wave_commit(chan, lib_version)
        self._wave_open[chan] = False
        res.expected = "波形库提交到 SRAM"
        res.actual = f"result={resp.result} total_bytes={resp.total_bytes} " \
                     f"crc=0x{resp.lib_crc16:04X} {resp.wave_count} 条 " \
                     f"base=0x{resp.base_addr:04X}"
        res.note = "；".join(f"id{e.wave_id}:{e.start}-{e.end}" for e in resp.entries)
        res.status = STATUS_PASS if resp.result == 0 else STATUS_FAIL

    def _op_sweep_amp(self, step: Step, res: StepResult, run: RunResult) -> None:
        p = step.params
        chan = self._require_chan(step)
        mode_value = p.get("mode", 0)
        if isinstance(mode_value, str):
            mode = 0 if mode_value.strip().upper() == "CONT" else 1
        else:
            mode = self._int(mode_value, 0) or 0
        from_lvl = self._require_int(step, "from")
        to_lvl = self._require_int(step, "to")
        step_lvl = self._require_int(step, "step")
        if step_lvl == 0:
            raise ValueError("sweep_amp 的 step 不可为 0")
        resp = self.device.sweep_amp(chan, mode, from_lvl, to_lvl, step_lvl,
                                     self._int(p.get("dwell_ms"), 200) or 200,
                                     self._int(p.get("settle_ms"), 100) or 0)
        res.expected = f"幅度扫描 {from_lvl}→{to_lvl} 步长 {step_lvl}" \
                       f"（{'CONT' if mode == 0 else 'RAM'}）"
        res.actual = f"accepted={resp.accepted} 共 {resp.total_steps} 步"
        note_parts = []
        wait_ms = self._int(p.get("wait_ms"), 0) or 0
        if wait_ms and self._has_events:
            done = self._wait_event(C.EVT_SWEEP_DONE, chan, wait_ms)
            if done is not None:
                note_parts.append(f"扫描完成：{done.steps} 步，status={done.status}")
            else:
                note_parts.append(f"{wait_ms} ms 内未收到 EVT_SWEEP_DONE")
        res.note = "；".join(note_parts)
        res.status = STATUS_PASS if resp.accepted else STATUS_FAIL
        if res.status == STATUS_PASS:
            self._sleep_ms(50)

    # ---- 通道探测 ----

    def _op_ch_probe(self, step: Step, res: StepResult) -> None:
        chan = self._require_chan(step)
        info = self.device.ch_probe(chan)
        chipid = (info.chipid_h << 8) | info.chipid_l
        res.expected = f"CH{chan} 在线且 CHIPID=0x{C.AW_CHIPID_EXPECTED:04X}"
        res.actual = f"present={info.present} CHIPID=0x{chipid:04X} " \
                     f"vreg_ok={info.vreg_ok}"
        res.status = STATUS_PASS if info.present else STATUS_FAIL
        if info.present and chipid != C.AW_CHIPID_EXPECTED:
            res.note = f"CHIPID 与期望 0x{C.AW_CHIPID_EXPECTED:04X} 不符，" \
                       f"可能是 AW86234AFCR 等变体"

    def _op_ch_probe_all(self, step: Step, res: StepResult) -> None:
        p = step.params
        resp = self.device.ch_probe_all()
        entries = []
        for chan, hid, lid, status in resp.entries:
            chipid = (hid << 8) | lid
            flag = "" if chipid == C.AW_CHIPID_EXPECTED else "（CHIPID 不符）"
            entries.append(f"CH{chan}: 0x{chipid:04X}{flag}"
                           + ("" if status == 0 else f" 错误码 {status}"))
        res.expected = p.get("note") or "8 路在线状态与 CHIPID"
        res.actual = f"present_mask=0x{resp.present_mask:02X}，" \
                     f"在线 {bin(resp.present_mask).count('1')} 路"
        res.note = "；".join(entries)
        want_mask = self._int(p.get("expect_mask"))
        want_chipid = self._int(p.get("expect_chipid"), C.AW_CHIPID_EXPECTED)
        bad = [f"CH{c}" for c, h, l, _s in resp.entries
               if want_chipid is not None and ((h << 8) | l) != want_chipid]
        if want_mask is not None and (resp.present_mask & want_mask) != want_mask:
            res.status = STATUS_FAIL
            res.note += f"；期望至少在线 0x{want_mask:02X}"
        elif bad:
            res.status = STATUS_FAIL
            res.note += "；CHIPID 不符：" + "、".join(bad)
        else:
            res.status = STATUS_PASS
        missing = [c for c in (p.get("expect_channels") or [])
                   if not resp.present_mask & (1 << int(c))]
        if missing:
            res.status = STATUS_FAIL
            res.note += "；以下通道不在线：" + "、".join(f"CH{c}" for c in missing)

    # ---- 数据构造 ----

    def _build_samples(self, params: Dict[str, Any], rate_default: int = C.RATE_24K,
                       start_index: int = 0) -> List[int]:
        """按参数构造样本：``samples`` 数组 / ``csv`` 文件 / 生成器参数 / ``pattern``。"""
        if isinstance(params.get("samples"), (list, tuple)):
            return [clip(int(v)) for v in params["samples"]]
        if params.get("csv"):
            return load_csv(str(params["csv"]))
        rate_code = self._rate_code(params.get("rate", params.get("sample_rate")))
        rate_hz = C.RATE_HZ.get(rate_code, 24000)
        if params.get("shape") is not None:
            shape = shape_code(params["shape"])
            freq = self._num(params.get("freq_hz"), 175.0) or 175.0
            amp = int(self._num(params.get("amplitude"), 100.0) or 0)
            cycles = int(self._num(params.get("cycles"), 1.0) or 1)
            return generate(shape, freq, amp, cycles, rate_hz,
                            float(self._num(params.get("phase_deg"), 0.0) or 0.0))
        # RTP 流式：按 pattern 直接合成 count 个样本
        pattern = str(params.get("pattern", "sine")).strip().lower()
        count = int(self._num(params.get("count"), 256.0) or 256)
        freq = self._num(params.get("freq_hz"), 175.0) or 175.0
        amp = int(self._num(params.get("amplitude"), 90.0) or 0)
        out: List[int] = []
        for i in range(max(1, count)):
            index = start_index + i
            if pattern in ("silence", "zero", "静音"):
                out.append(0)
            elif pattern in ("ramp", "锯齿"):
                out.append(clip(amp * (2.0 * ((index % 100) / 100.0) - 1.0)))
            else:                                        # sine
                import math
                out.append(clip(amp * math.sin(2.0 * math.pi * freq * index / float(rate_hz))))
        _ = rate_default
        return out


# --------------------------------------------------------------------------
# 模块级小工具
# --------------------------------------------------------------------------


def rate_code_from_hz(hz: int) -> int:
    """把采样率（Hz）映射成协议编码。"""
    if hz == 12000:
        return C.RATE_12K
    if hz == 24000:
        return C.RATE_24K
    if hz == 48000:
        return C.RATE_48K
    raise ValueError(f"未知采样率：{hz}")


def _profile_note(prof: P.Profile) -> str:
    parts = [f"mode={C.mode_name(prof.mode)}", f"gain={prof.gain}",
             f"brk_en={prof.brk_en}", f"stop_mode={prof.stop_mode}"]
    if prof.mode == C.MODE_CONT:
        parts += [f"f_pre={prof.f_pre}（F0≈{prof.f0_hz:.1f} Hz）",
                  f"drv1_lvl={prof.drv1_lvl}", f"drv2_lvl={prof.drv2_lvl}",
                  f"track_en={prof.track_en}", f"en_f0_det={prof.en_f0_det}"]
    elif prof.mode == C.MODE_TRIG:
        parts += [f"trig1_seq_p={prof.trig1_seq_p}", f"trig1_lev={prof.trig1_lev}",
                  f"trig1_stop={prof.trig1_stop}", f"trig1_brk={prof.trig1_brk}"]
    elif prof.mode == C.MODE_RTP:
        parts += [f"wavdat_mode={C.RATE_NAMES.get(prof.wavdat_mode)}",
                  f"base_addr=0x{prof.base_addr:04X}"]
    else:
        parts += [f"wavseq={list(prof.wavseq)}", f"mainloop={prof.mainloop}"]
    return "；".join(parts)


def _snapshot_text(snap: K.Snapshot, d2s_gain: int = 8) -> str:
    chipid = (snap.chipid_h << 8) | snap.chipid_l
    parts = [f"CHIPID=0x{chipid:04X}",
             f"模式={C.mode_name(snap.mode)}",
             f"GLB_STATE={snap.glb_state_name}",
             f"playing={snap.playing}"]
    f0 = snap.f0_hz
    if f0 is not None:
        parts.append(f"F0={f0:.1f} Hz")
    cont = snap.cont_f0_hz
    if cont is not None:
        parts.append(f"CONT_F0={cont:.1f} Hz")
    parts.append(f"VBAT={snap.vbat_v():.2f} V")
    rl = snap.rl_ohm(d2s_gain)
    if rl:
        parts.append(f"RL={rl:.1f} Ω")
    parts.append(f"SYSST=0x{snap.sysst:02X} SYSST2=0x{snap.sysst2:02X}")
    return " · ".join(parts)


def _trig_params_from(params: Dict[str, Any]) -> K.TrigParams:
    """由脚本参数构造 ``TrigParams``（未给出的字段用 dataclass 默认值）。"""
    import dataclasses as _dc

    base = K.TrigParams()
    kwargs: Dict[str, Any] = {}
    for f in _dc.fields(K.TrigParams):
        if f.name in params and params[f.name] is not None:
            value = params[f.name]
            if isinstance(value, bool):
                kwargs[f.name] = int(value)
            elif isinstance(value, str):
                text = value.strip()
                try:
                    kwargs[f.name] = int(text, 0)
                except ValueError:
                    kwargs[f.name] = getattr(base, f.name)
            else:
                kwargs[f.name] = int(value)
    obj = K.TrigParams(**kwargs)
    trigger = params.get("trigger") or params.get("trigger_mode")
    if isinstance(trigger, str) and trigger.strip():
        obj.apply_trigger_mode(trigger.strip())
    return obj
