# -*- coding: utf-8 -*-
"""测试脚本格式定义与校验（`05` 文档 §5.9）。

脚本是纯 JSON，一个例子：

.. code-block:: json

    {
      "name": "A9 幅度线性度",
      "matrix_ids": ["A9"],
      "description": "CONT 模式下逐档改 DRV1_LVL，记录回读值与人工填写的 Vpp",
      "channels": [0],
      "steps": [
        {"op": "set_mode", "chan": 0, "mode": "CONT"},
        {"op": "prompt", "message": "请记录示波器上的 Vpp (V)", "field": "vpp", "unit": "V"}
      ]
    }

字段约定：

* :class:`Step` 的 ``params`` 是**该步 JSON 里除 ``op`` 以外的全部字段**（原样保留，
  包括可选的嵌套 ``params`` 对象），``raw`` 则是原始字典的副本。
  ``set_profile`` 这类命令既支持 ``{"params": {...}}`` 写法，也支持把字段直接
  写在步上，引擎两者都会读。
* :func:`validate_script` 返回**中文问题列表**，空列表表示脚本合法；
  :func:`parse_script` 在非法时抛 ``ValueError``（把问题列表拼成一条消息）。

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import profiles as P

# --------------------------------------------------------------------------
# 支持的操作（`05` 文档 §5.9 的"必须支持的 op 集合" + 若干便利 op）
# --------------------------------------------------------------------------

#: 必须支持的 op（接口约定 §3）
REQUIRED_OPS = (
    "set_mode", "set_profile", "get_profile", "reg_read", "reg_write", "reg_update",
    "wave_gen", "wave_add", "wave_commit", "play", "stop", "stop_all",
    "trig_pulse", "trig_pattern", "trig_level", "trig_park",
    "f0", "det_read", "snapshot", "sweep_amp", "sweep_freq",
    "wait", "prompt", "assert_read", "assert_equals", "assert_range",
    "record", "loop", "repeat", "log",
)

#: 额外支持的便利 op（协议里存在、界面/脚本常用）
EXTRA_OPS = (
    "ping", "get_caps", "get_status", "get_safety", "set_safety", "clear_errors",
    "bus_probe", "bus_set_speed", "bus_set_pullup", "bus_recover", "tca_reset",
    "tca_set_mask", "tca_get_mask",
    "ch_probe", "ch_probe_all", "ch_get_info",
    "aw_softrst", "aw_wake", "aw_standby", "aw_init_default", "aw_get_state",
    "set_default_profile", "set_gain", "set_vbat", "set_cont_live",
    "set_ram_params", "set_cont_params", "set_rtp_params", "set_trig_params",
    "rtp_data", "wave_begin", "wave_abort", "wave_erase", "wave_queue",
    "wave_readback", "wave_get_library", "play_multi", "get_play_state",
    "sweep_abort", "mon_start", "mon_stop", "test_run", "test_abort", "selftest",
    "wait_play_done", "wait_event",
)

#: 全集
OPS = frozenset(REQUIRED_OPS + EXTRA_OPS)

#: 合法判定值
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"

#: ``chan`` 允许的广播写法
CHAN_ALL_WORDS = ("all", "全部", "广播")
CHAN_ALL_VALUE = 0xFF

#: 采样率下限/上限（Hz），用于 wave_* / rtp_data 校验
SAMPLE_RATE_MIN = 1000
SAMPLE_RATE_MAX = 48000

#: 采样率写法 -> 协议编码（协议 §9.8：0=24K 1=48K 2/3=12K）
RATE_WORDS: Dict[str, int] = {
    "12k": 2, "12khz": 2, "12000": 2,
    "24k": 0, "24khz": 0, "24000": 0,
    "48k": 1, "48khz": 1, "48000": 1,
}

#: 需要通道号的 op（校验 chan 用）
_CHAN_OPS = frozenset({
    "set_mode", "set_profile", "get_profile", "reg_read", "reg_write", "reg_update",
    "reg_read_multi", "reg_dump", "stream_write",
    "wave_gen", "wave_add", "wave_commit", "wave_abort", "wave_erase", "wave_queue",
    "wave_readback", "wave_get_library", "wave_begin",
    "play", "stop", "get_play_state", "f0", "f0_start", "f0_result", "det_read",
    "snapshot", "sweep_amp", "sweep_freq", "record",
    "ch_probe", "ch_get_info", "aw_softrst", "aw_wake", "aw_standby",
    "aw_init_default", "aw_get_state", "set_default_profile",
    "set_gain", "set_vbat", "set_cont_live", "set_ram_params", "set_cont_params",
    "set_rtp_params", "set_trig_params", "rtp_data", "assert_read",
    "wait_play_done",
})

#: 需要通道掩码的 op
_MASK_OPS = frozenset({
    "play_multi", "trig_pulse", "trig_pattern", "trig_level", "mon_start", "mon_stop",
})

#: 断言类 op
ASSERT_OPS = frozenset({"assert_read", "assert_equals", "assert_range"})

#: 合法的 Profile 字段名：标量字段来自 ``profiles.FIELD_SPECS``，
#: 另有三个数组字段（``wavseq`` / ``seq_loop`` / ``reserved_ram``）不在范围表里。
PROFILE_FIELDS = frozenset(P.FIELD_SPECS) | {"wavseq", "seq_loop", "reserved_ram"}

#: 数组型 Profile 字段 -> 期望长度
PROFILE_LIST_LEN = {"wavseq": 8, "seq_loop": 4, "reserved_ram": 6}


#: 断言类 op 允许的"被测值来源"字段
VALUE_SOURCE_KEYS = ("actual", "value", "field", "manual", "previous", "use_previous")


def is_assert_op(op: str) -> bool:
    """是否为断言类 op。"""
    return op in ASSERT_OPS


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Step:
    """脚本中的一步。

    ``params`` 是"除 ``op`` 以外的全部字段"的字典视图，``raw`` 是原始 JSON 对象。
    """

    op: str
    params: Dict[str, Any] = field(default_factory=dict)
    index: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def profile_fields(self) -> Dict[str, Any]:
        """取出 ``set_profile`` 的 Profile 字段（支持嵌套 ``params`` 与直接写法）。

        控制类字段（``chan`` / ``params`` / ``matrix_id`` 等）不会混进 Profile。
        """
        control = {"chan", "params", "matrix_id", "expect_error", "note", "comment"}
        out: Dict[str, Any] = {}
        nested = self.params.get("params")
        if isinstance(nested, dict):
            out.update(nested)
        for key, value in self.params.items():
            if key in control or key == "params":
                continue
            out[key] = value
        return out


@dataclass
class TestScript:
    """一份测试脚本。"""

    name: str = ""
    matrix_ids: List[str] = field(default_factory=list)
    description: str = ""
    channels: List[int] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def matrix_id(self) -> str:
        """主矩阵编号（报告里给步骤用的默认值）。"""
        return self.matrix_ids[0] if self.matrix_ids else ""

    def to_dict(self) -> Dict[str, Any]:
        """还原成可写盘的字典（步骤用原始 JSON 对象）。"""
        data: Dict[str, Any] = {
            "name": self.name,
            "matrix_ids": list(self.matrix_ids),
            "description": self.description,
            "channels": list(self.channels),
            "steps": [dict(s.raw) if s.raw else _step_to_raw(s) for s in self.steps],
        }
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


def _step_to_raw(step: Step) -> Dict[str, Any]:
    raw = {"op": step.op}
    raw.update(step.params)
    return raw


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_int(value: Any) -> Optional[int]:
    """把 ``12`` / ``"0x1C"`` / ``"28"`` 解析成整数；失败返回 ``None``。"""
    if _is_int(value):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            try:
                return int(text, 16)
            except ValueError:
                return None
    return None


def parse_error_spec(value: Any) -> Optional[Any]:
    """把 ``expect_error`` 解析成 ``(错误码集合)`` 形式；无法识别返回 ``None``。

    支持：``true``（任意错误）、错误码数字、``"0x10"``、``"ERR_SAFETY_LIMIT"``、
    以及上述任意组合的列表。
    """
    from ..protocol import commands as C

    if value is True:
        return True
    if value is False or value is None:
        return None
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            parsed = parse_error_spec(item)
            if parsed is None or parsed is True:
                continue
            out.extend(parsed)
        return out or None
    code = _parse_int(value)
    if code is not None and 0 <= code <= 0xFF:
        return [code]
    if isinstance(value, str):
        name = value.strip().upper()
        for code, err_name in C.ERR_NAMES.items():
            if name == err_name.upper():
                return [code]
    return None


def _check_chan(problems: List[str], prefix: str, params: Dict[str, Any],
                key: str = "chan", required: bool = True) -> None:
    if key not in params or params[key] is None:
        if required:
            problems.append(f"{prefix}：缺少必填参数 {key}（0..7，或 \"all\"）")
        return
    value = params[key]
    if isinstance(value, str):
        if value.strip().lower() in CHAN_ALL_WORDS:
            return
        problems.append(f"{prefix}：{key} 写法非法（{value!r}），应为 0..7 的整数或 \"all\"")
        return
    if not _is_int(value):
        problems.append(f"{prefix}：{key} 必须是整数，实际 {value!r}")
        return
    if value == CHAN_ALL_VALUE:
        return
    if not (0 <= value <= 7):
        problems.append(f"{prefix}：{key}={value} 超出通道范围 0..7")


def _check_mask(problems: List[str], prefix: str, params: Dict[str, Any]) -> None:
    key = "chan_mask" if "chan_mask" in params else ("mask" if "mask" in params else None)
    if key is None:
        problems.append(f"{prefix}：缺少必填参数 chan_mask（0..255 的通道掩码）")
        return
    value = params[key]
    if not _is_int(value):
        problems.append(f"{prefix}：{key} 必须是整数掩码，实际 {value!r}")
        return
    if not (0 <= value <= 0xFF):
        problems.append(f"{prefix}：{key}={value} 超出掩码范围 0..255")


def _check_num(problems: List[str], prefix: str, params: Dict[str, Any], key: str,
               lo: Optional[float] = None, hi: Optional[float] = None,
               required: bool = True) -> None:
    if key not in params or params[key] is None:
        if required:
            problems.append(f"{prefix}：缺少必填参数 {key}")
        return
    value = params[key]
    if isinstance(value, str):
        parsed = _parse_int(value)
        if parsed is None:
            problems.append(f"{prefix}：参数 {key} 必须是数字，实际 {value!r}")
            return
        value = parsed
    if not _is_num(value):
        problems.append(f"{prefix}：参数 {key} 必须是数字，实际 {value!r}")
        return
    if lo is not None and value < lo:
        problems.append(f"{prefix}：参数 {key}={value} 小于下限 {lo}")
    if hi is not None and value > hi:
        problems.append(f"{prefix}：参数 {key}={value} 大于上限 {hi}")


def _check_rate(problems: List[str], prefix: str, params: Dict[str, Any],
                key: str = "rate", required: bool = False) -> None:
    if key in params and params[key] is not None:
        value = params[key]
        if isinstance(value, str):
            if value.strip().lower() not in RATE_WORDS:
                problems.append(f"{prefix}：参数 {key}={value!r} 无法识别"
                                f"（可用 12K/24K/48K 或编码 0/1/2）")
            return
        if _is_int(value):
            if value not in (0, 1, 2, 3):
                problems.append(f"{prefix}：参数 {key}={value} 超出采样率编码 0..3")
            return
        problems.append(f"{prefix}：参数 {key} 必须是采样率名或编码，实际 {value!r}")
        return
    if required and "sample_rate" not in params:
        problems.append(f"{prefix}：缺少必填参数 {key}（12K/24K/48K 或编码 0/1/2）")


def _check_expect(problems: List[str], prefix: str, params: Dict[str, Any],
                  key: str = "expect", required: bool = True) -> None:
    if key not in params or params[key] is None:
        if required:
            problems.append(f"{prefix}：缺少必填参数 {key}（期望值）")
        return
    value = params[key]
    if isinstance(value, (list, tuple)):
        if not value:
            problems.append(f"{prefix}：{key} 不能是空列表")
        return
    if isinstance(value, bool):
        return
    if _is_num(value):
        return
    if isinstance(value, str):
        text = value.strip()
        if not text:
            problems.append(f"{prefix}：{key} 不能为空")
        return
    problems.append(f"{prefix}：{key} 类型不支持（{type(value).__name__}）")


def _check_profile_fields(problems: List[str], label: str, fields: Dict[str, Any]) -> None:
    """校验一组 Profile 字段名与取值形态。"""
    for key, value in fields.items():
        if key not in PROFILE_FIELDS:
            problems.append(f"{label}：未知 Profile 字段「{key}」")
            continue
        if key in PROFILE_LIST_LEN:
            want = PROFILE_LIST_LEN[key]
            if not isinstance(value, (list, tuple)):
                problems.append(f"{label}：Profile 字段 {key} 必须是长度为 {want} 的数组")
            elif len(value) != want:
                problems.append(f"{label}：Profile 字段 {key} 需要 {want} 项，"
                                f"实际 {len(value)}")
            else:
                for item in value:
                    if not _is_int(item) or not (0 <= item <= 0xFF):
                        problems.append(f"{label}：Profile 字段 {key} 的元素必须是 "
                                        f"0..255 的整数，实际 {item!r}")
        elif not _is_int(value) and not isinstance(value, str):
            problems.append(f"{label}：Profile 字段 {key} 必须是整数，实际 {value!r}")


def _validate_steps(data_steps: Any, problems: List[str], depth: int = 0) -> None:
    """逐个校验步骤列表（``repeat``/``loop`` 会递归）。"""
    if not isinstance(data_steps, list):
        problems.append("steps 必须是数组")
        return
    if not data_steps:
        problems.append("steps 不能为空（至少要有一步）")
        return
    for pos, raw in enumerate(data_steps, 1):
        prefix = f"第 {pos} 步"
        if not isinstance(raw, dict):
            problems.append(f"{prefix} 必须是 JSON 对象，实际 {type(raw).__name__}")
            continue
        op = raw.get("op")
        if not op:
            problems.append(f"{prefix} 缺少 op 字段")
            continue
        if not isinstance(op, str):
            problems.append(f"{prefix} 的 op 必须是字符串，实际 {op!r}")
            continue
        op_name = op.strip()
        if op_name not in OPS:
            problems.append(f"{prefix}的 op「{op_name}」未知（不在支持列表里）")
            continue

        label = f"{prefix}（{op_name}）"
        params: Dict[str, Any] = {k: v for k, v in raw.items() if k != "op"}

        # ---- 通道号 / 掩码 ----
        if op_name in _CHAN_OPS:
            _check_chan(problems, label, params)
        if op_name in _MASK_OPS:
            _check_mask(problems, label, params)
        if op_name in ("set_mode", "set_profile", "get_profile", "play", "stop",
                       "record", "snapshot", "det_read", "f0", "get_status"):
            if not _is_int(params.get("chan")) and params.get("chan") is not None \
                    and not isinstance(params.get("chan"), str):
                problems.append(f"{label}：chan 类型非法")

        # ---- 各 op 的必填参数 ----
        if op_name == "set_mode":
            mode = params.get("mode")
            if mode is None:
                problems.append(f"{label}：缺少必填参数 mode（RAM/RTP/CONT/TRIG 或 0..3）")
            elif isinstance(mode, str):
                if mode.strip().upper() not in ("RAM", "RTP", "CONT", "TRIG"):
                    problems.append(f"{label}：mode={mode!r} 无法识别"
                                    f"（可用 RAM/RTP/CONT/TRIG）")
            elif not _is_int(mode) or not (0 <= mode <= 3):
                problems.append(f"{label}：mode 必须是 RAM/RTP/CONT/TRIG 或 0..3")

        elif op_name == "set_profile":
            nested = params.get("params")
            if nested is not None and not isinstance(nested, dict):
                problems.append(f"{label}：params 必须是对象（Profile 字段表）")
            elif nested is None:
                extra = {k: v for k, v in params.items()
                         if k not in ("chan", "matrix_id", "note", "expect_error")}
                if not extra:
                    problems.append(f"{label}：缺少 Profile 字段（写在 params 里或直接写在步上）")
                else:
                    _check_profile_fields(problems, label, extra)
            else:
                _check_profile_fields(problems, label, nested)

        elif op_name in ("reg_read", "reg_write", "reg_update"):
            _check_num(problems, label, params, "addr", 0, 0xFF)
            if op_name == "reg_write" and "data" not in params and "value" not in params:
                problems.append(f"{label}：缺少必填参数 value（或 data 数组）")
            if op_name == "reg_update":
                _check_num(problems, label, params, "mask", 0, 0xFF)
                _check_num(problems, label, params, "value", 0, 0xFF)

        elif op_name == "wave_gen":
            _check_num(problems, label, params, "wave_id", 1, 127)
            shape = params.get("shape")
            if shape is None:
                problems.append(f"{label}：缺少必填参数 shape（波形类型）")
            else:
                from ..waveform.generator import shape_code
                try:
                    shape_code(shape)
                except ValueError as exc:
                    problems.append(f"{label}：shape 非法（{exc}）")
            _check_num(problems, label, params, "freq_hz", 1, 100000)
            _check_num(problems, label, params, "amplitude", 0, 127)
            _check_num(problems, label, params, "cycles", 1, 100000)
            _check_rate(problems, label, params, required=True)

        elif op_name == "wave_add":
            _check_num(problems, label, params, "wave_id", 1, 127)
            if "samples" not in params and "csv" not in params:
                if params.get("shape") is None:
                    problems.append(f"{label}：需要 samples / csv / shape 三者之一来提供波形数据")
                else:
                    from ..waveform.generator import shape_code
                    try:
                        shape_code(params.get("shape"))
                    except ValueError as exc:
                        problems.append(f"{label}：shape 非法（{exc}）")

        elif op_name == "wave_queue":
            ids = params.get("ids")
            if not isinstance(ids, list) or not ids:
                problems.append(f"{label}：缺少必填参数 ids（波形号数组，1..8 个）")
            elif len(ids) > 8:
                problems.append(f"{label}：ids 最多 8 个，实际 {len(ids)}")

        elif op_name == "play":
            _check_num(problems, label, params, "timeout_ms", 0, 65535, required=False)

        elif op_name == "trig_pulse":
            _check_num(problems, label, params, "width_us", 1, 65535)
            _check_num(problems, label, params, "count", 1, 32, required=False)
            _check_num(problems, label, params, "period_ms", 0, 65535, required=False)

        elif op_name == "trig_pattern":
            _check_num(problems, label, params, "on_us", 1, 65535)
            _check_num(problems, label, params, "off_us", 0, 65535, required=False)
            _check_num(problems, label, params, "count", 1, 65535, required=False)

        elif op_name == "trig_level":
            _check_num(problems, label, params, "level", 0, 1, required=False)
            _check_num(problems, label, params, "hold_ms", 1, 65535)

        elif op_name == "det_read":
            seq = _parse_int(params.get("seq"))
            if seq is None:
                problems.append(f"{label}：缺少必填参数 seq（0=VBAT 3=RL 4=OS）")
            elif seq not in (0, 3, 4):
                problems.append(f"{label}：seq={seq} 非法（只支持 0/3/4）")

        elif op_name == "sweep_amp":
            sweep_mode = params.get("mode")
            if isinstance(sweep_mode, str) and \
                    sweep_mode.strip().upper() not in ("CONT", "RAM"):
                problems.append(f"{label}：mode={sweep_mode!r} 无法识别（0=CONT 1=RAM）")
            elif sweep_mode is not None and not isinstance(sweep_mode, str):
                _check_num(problems, label, params, "mode", 0, 1)
            _check_num(problems, label, params, "from", 0, 255)
            _check_num(problems, label, params, "to", 0, 255)
            step = params.get("step")
            if step is None:
                problems.append(f"{label}：缺少必填参数 step")
            else:
                _check_num(problems, label, params, "step", 1, 255)
                if _is_num(step) and step == 0:
                    problems.append(f"{label}：step 不可为 0")
            _check_num(problems, label, params, "dwell_ms", 1, 65535, required=False)
            _check_num(problems, label, params, "settle_ms", 0, 65535, required=False)

        elif op_name == "sweep_freq":
            _check_num(problems, label, params, "from_hz", 1, 65535)
            _check_num(problems, label, params, "to_hz", 1, 65535)
            step_hz = params.get("step_hz")
            if step_hz is None:
                problems.append(f"{label}：缺少必填参数 step_hz")
            else:
                _check_num(problems, label, params, "step_hz", 1, 65535)
                if _is_num(step_hz) and step_hz == 0:
                    problems.append(f"{label}：step_hz 不可为 0")
            _check_num(problems, label, params, "dwell_ms", 1, 65535, required=False)

        elif op_name == "wait":
            _check_num(problems, label, params, "ms", 0, 600000)
            if _is_int(params.get("ms")) and params["ms"] > 600000:
                problems.append(f"{label}：ms 过大（上限 600000）")

        elif op_name == "prompt":
            if not str(params.get("message") or "").strip():
                problems.append(f"{label}：缺少必填参数 message（提示文案）")
            if not str(params.get("field") or "").strip():
                problems.append(f"{label}：缺少必填参数 field（记录字段名）")

        elif op_name == "assert_read":
            _check_num(problems, label, params, "addr", 0, 0xFF)
            _check_expect(problems, label, params)

        elif op_name == "assert_equals":
            _check_expect(problems, label, params)
            if not any(k in params for k in VALUE_SOURCE_KEYS):
                problems.append(f"{label}：需要 actual / value / field 之一作为被测值"
                                f"（或 previous=true 表示用上一步的实测值）")

        elif op_name == "assert_range":
            if not any(k in params for k in VALUE_SOURCE_KEYS):
                problems.append(f"{label}：需要 value / field 之一作为被测值"
                                f"（或 previous=true 表示用上一步的实测值）")
            _check_num(problems, label, params, "min", required=False)
            _check_num(problems, label, params, "max", required=False)
            if "min" not in params and "max" not in params:
                problems.append(f"{label}：至少要给出 min 或 max")
            lo, hi = params.get("min"), params.get("max")
            if _is_num(lo) and _is_num(hi) and lo > hi:
                problems.append(f"{label}：min={lo} 大于 max={hi}")

        elif op_name in ("loop", "repeat"):
            _check_num(problems, label, params, "count", 1, 10000)
            sub = params.get("steps")
            if not isinstance(sub, list) or not sub:
                problems.append(f"{label}：缺少必填参数 steps（要重复的步骤数组）")
            elif depth >= 8:
                problems.append(f"{label}：循环嵌套层级过深（上限 8 层）")
            else:
                _validate_steps(sub, problems, depth + 1)

        elif op_name == "log":
            if not str(params.get("text") or "").strip():
                problems.append(f"{label}：缺少必填参数 text")

        elif op_name == "wait_event":
            if not str(params.get("event") or "").strip():
                problems.append(f"{label}：缺少必填参数 event（事件名，如 EVT_SAFETY_TRIP）")

        # ---- expect_error 可解析性 ----
        if "expect_error" in params and params["expect_error"] is not None:
            if parse_error_spec(params["expect_error"]) is None:
                problems.append(f"{label}：expect_error={params['expect_error']!r} 无法解析"
                                f"（可用 true / 错误码数字 / ERR_xxx 名字）")


def validate_script(data: Dict[str, Any]) -> List[str]:
    """校验脚本（未解析的原始字典），返回中文问题列表，空列表表示合法。"""
    problems: List[str] = []
    if not isinstance(data, dict):
        return [f"脚本必须是 JSON 对象，实际 {type(data).__name__}"]

    name = data.get("name")
    if name is not None and not isinstance(name, str):
        problems.append("name 必须是字符串")

    matrix_ids = data.get("matrix_ids")
    if matrix_ids is not None:
        if not isinstance(matrix_ids, list):
            problems.append("matrix_ids 必须是数组")
        else:
            for item in matrix_ids:
                if not isinstance(item, str) or not item.strip():
                    problems.append(f"matrix_ids 里的元素必须是非空字符串，实际 {item!r}")

    channels = data.get("channels")
    if channels is not None:
        if not isinstance(channels, list):
            problems.append("channels 必须是数组")
        else:
            for item in channels:
                if not _is_int(item) or not (0 <= item <= 7):
                    problems.append(f"channels 里的通道号必须在 0..7，实际 {item!r}")

    if "steps" not in data:
        problems.append("缺少 steps 字段（必须是数组）")
    else:
        _validate_steps(data["steps"], problems, 0)
    return problems


# --------------------------------------------------------------------------
# 解析 / 存取
# --------------------------------------------------------------------------


def _make_step(raw: Dict[str, Any], index: int) -> Step:
    op = str(raw.get("op", "")).strip()
    params = {k: v for k, v in raw.items() if k != "op"}
    return Step(op=op, params=params, index=index, raw=dict(raw))


def parse_script(data: Dict[str, Any], path: Optional[str] = None) -> TestScript:
    """把字典解析成 :class:`TestScript`；非法时抛 ``ValueError``（消息为中文问题列表）。"""
    problems = validate_script(data)
    if problems:
        raise ValueError("脚本不合法：" + "；".join(problems))

    script = TestScript(
        name=str(data.get("name", "")).strip(),
        matrix_ids=[str(m) for m in data.get("matrix_ids", [])],
        description=str(data.get("description", "")),
        channels=[int(c) for c in data.get("channels", [])],
        path=path,
        metadata=dict(data.get("metadata", {}) or {}),
    )
    script.steps = [_make_step(raw, i) for i, raw in enumerate(data["steps"], 1)]
    return script


def load_script(path: str) -> TestScript:
    """从 JSON 文件读入脚本。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return parse_script(data, path=path)


def save_script(script: TestScript, path: str) -> None:
    """把脚本写回 JSON（UTF-8，中文不转义）。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(script.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------
# 预设脚本
# --------------------------------------------------------------------------


def preset_scripts_dir() -> str:
    """随包预设脚本目录（``awtest/resources/scripts``）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "resources", "scripts")


def list_preset_scripts(directory: Optional[str] = None) -> List[Tuple[str, str]]:
    """列出预设脚本，返回 ``[(显示名, 路径)]``。

    显示名取 JSON 的 ``name`` 字段（读取失败时退回文件名）。默认查
    ``awtest/resources/scripts/``。
    """
    folder = directory or preset_scripts_dir()
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(folder):
        return out
    for filename in sorted(os.listdir(folder)):
        if not filename.lower().endswith(".json"):
            continue
        path = os.path.join(folder, filename)
        display = os.path.splitext(filename)[0]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                name = str(data.get("name", "")).strip()
                if name:
                    display = name
        except Exception:                            # 坏文件不该让列表整体失败
            display = os.path.splitext(filename)[0]
        out.append((display, path))
    return out
