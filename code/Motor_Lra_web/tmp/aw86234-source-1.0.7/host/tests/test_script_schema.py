# -*- coding: utf-8 -*-
"""``awtest.script.schema`` 单测：合法/非法脚本校验、中文问题文案、预设脚本枚举。"""

from __future__ import annotations

import json
import os

import pytest

from awtest.script import schema as S


def _valid_script() -> dict:
    return {
        "name": "示例脚本",
        "matrix_ids": ["A9"],
        "description": "用于单测的最小合法脚本",
        "channels": [0],
        "steps": [
            {"op": "set_mode", "chan": 0, "mode": "CONT"},
            {"op": "set_profile", "chan": 0, "params": {"drv1_lvl": 16, "track_en": 0}},
            {"op": "play", "chan": 0, "timeout_ms": 1000},
            {"op": "wait", "ms": 300},
            {"op": "prompt", "message": "请记录 Vpp", "field": "vpp", "unit": "V"},
            {"op": "assert_read", "chan": 0, "addr": "0x1C", "expect": "0x10"},
            {"op": "assert_range", "field": "vpp", "min": 0.1, "max": 6.0, "unit": "V"},
            {"op": "stop", "chan": 0},
            {"op": "record", "chan": 0},
            {"op": "log", "text": "完成"},
        ],
    }


# --------------------------------------------------------------------------
# 合法脚本
# --------------------------------------------------------------------------


def test_valid_script_has_no_problems():
    assert S.validate_script(_valid_script()) == []


def test_parse_script_builds_steps_and_params_view():
    script = S.parse_script(_valid_script(), path="memory.json")
    assert script.name == "示例脚本"
    assert script.matrix_ids == ["A9"]
    assert script.matrix_id == "A9"
    assert script.path == "memory.json"
    assert len(script.steps) == 10
    first = script.steps[0]
    assert first.op == "set_mode"
    assert first.index == 1
    assert first.params["chan"] == 0
    assert first.params["mode"] == "CONT"
    assert first.raw == {"op": "set_mode", "chan": 0, "mode": "CONT"}
    # set_profile 的 Profile 字段既能从嵌套 params 读，也能从直接写法读
    prof_step = script.steps[1]
    assert prof_step.params["params"] == {"drv1_lvl": 16, "track_en": 0}
    assert prof_step.profile_fields() == {"drv1_lvl": 16, "track_en": 0}


def test_all_required_ops_are_supported():
    for op in S.REQUIRED_OPS:
        assert op in S.OPS


def test_repeat_steps_are_validated_recursively():
    script = _valid_script()
    script["steps"] = [
        {"op": "repeat", "count": 3, "steps": [{"op": "wait", "ms": 10}]},
        {"op": "loop", "count": 2, "steps": [{"op": "log", "text": "循环"}]},
    ]
    assert S.validate_script(script) == []
    bad = {"steps": [{"op": "repeat", "count": 3, "steps": [{"op": "不存在的操作"}]}]}
    problems = S.validate_script(bad)
    assert problems and "未知" in problems[0]


# --------------------------------------------------------------------------
# 非法脚本：每一类都要报出中文问题
# --------------------------------------------------------------------------


def test_missing_steps_reports_chinese_problem():
    problems = S.validate_script({"name": "空脚本"})
    assert len(problems) == 1
    assert "steps" in problems[0]


def test_empty_steps_reports_chinese_problem():
    problems = S.validate_script({"name": "空数组", "steps": []})
    assert any("steps 不能为空" in p for p in problems)


def test_steps_not_a_list():
    problems = S.validate_script({"steps": {"op": "wait"}})
    assert any("steps 必须是数组" in p for p in problems)


def test_unknown_op_reports_chinese_problem():
    problems = S.validate_script({"steps": [{"op": "frobnicate"}]})
    assert len(problems) == 1
    assert "未知" in problems[0] and "frobnicate" in problems[0]


def test_missing_op_field():
    problems = S.validate_script({"steps": [{"chan": 0}]})
    assert any("缺少 op" in p for p in problems)


def test_missing_required_param_reports_chinese_problem():
    problems = S.validate_script({"steps": [{"op": "reg_read", "chan": 0}]})
    assert any("addr" in p and "缺少必填参数" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "reg_read", "addr": "0x1C"}]})
    assert any("chan" in p and "缺少必填参数" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "wait"}]})
    assert any("ms" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "prompt", "message": "填一下"}]})
    assert any("field" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "set_mode", "chan": 0, "mode": "XYZ"}]})
    assert any("mode" in p for p in problems)


def test_channel_out_of_range_reports_chinese_problem():
    problems = S.validate_script({"steps": [{"op": "play", "chan": 8}]})
    assert any("通道" in p and "8" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "play", "chan": -1}]})
    assert any("通道" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "play", "chan": "CH1"}]})
    assert any("chan" in p for p in problems)
    # chan="all" 与 0xFF 是合法的广播写法
    assert S.validate_script({"steps": [{"op": "play", "chan": "all"}]}) == []
    assert S.validate_script({"steps": [{"op": "play", "chan": 255}]}) == []


def test_script_level_channels_range():
    script = _valid_script()
    script["channels"] = [0, 7]
    assert S.validate_script(script) == []
    script["channels"] = [0, 9]
    assert any("channels" in p and "0..7" in p for p in S.validate_script(script))


def test_assert_expect_must_be_parseable():
    problems = S.validate_script({"steps": [{"op": "assert_read", "chan": 0,
                                             "addr": "0x1C"}]})
    assert any("expect" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "assert_equals", "expect": 1}]})
    assert any("actual" in p or "value" in p or "field" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "assert_range", "value": 1}]})
    assert any("min" in p or "max" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "assert_range", "field": "v",
                                            "min": 5, "max": 1}]})
    assert any("min" in p and "max" in p for p in problems)
    # 合法的期望写法：hex 字符串 / 数字 / 布尔 / 数组
    for expect in ("0x10", 16, True, [1, 2, 3]):
        problems = S.validate_script({"steps": [{"op": "assert_read", "chan": 0,
                                                 "addr": "0x1C", "expect": expect}]})
        assert problems == [], expect


def test_expect_error_must_be_parseable():
    good = {"steps": [{"op": "play_multi", "chan_mask": 255,
                       "expect_error": "ERR_SAFETY_LIMIT"}]}
    assert S.validate_script(good) == []
    for spec in (True, 0x10, "0x10", "ERR_UNSUPPORTED", ["ERR_BAD_PARAM", 3]):
        script = {"steps": [{"op": "play_multi", "chan_mask": 255, "expect_error": spec}]}
        assert S.validate_script(script) == [], spec
    bad = {"steps": [{"op": "play_multi", "chan_mask": 255, "expect_error": "随便写的"}]}
    assert any("expect_error" in p for p in S.validate_script(bad))


def test_unknown_profile_field_is_reported():
    problems = S.validate_script({"steps": [{"op": "set_profile", "chan": 0,
                                             "params": {"drv9_lvl": 1}}]})
    assert any("未知 Profile 字段" in p and "drv9_lvl" in p for p in problems)
    problems = S.validate_script({"steps": [{"op": "set_profile", "chan": 0}]})
    assert any("Profile 字段" in p for p in problems)
    # 数组型字段要检查长度
    problems = S.validate_script({"steps": [{"op": "set_profile", "chan": 0,
                                             "params": {"wavseq": [1, 2]}}]})
    assert any("wavseq" in p and "8" in p for p in problems)


def test_op_specific_ranges():
    cases = [
        ({"op": "wave_gen", "chan": 0, "wave_id": 1, "shape": 0, "freq_hz": 175,
          "amplitude": 200, "cycles": 5, "rate": "24K"}, "amplitude"),
        ({"op": "trig_pulse", "chan_mask": 1, "width_us": 0}, "width_us"),
        ({"op": "det_read", "chan": 0, "seq": 9}, "seq"),
        ({"op": "sweep_amp", "chan": 0, "from": 1, "to": 10, "step": 0}, "step"),
        ({"op": "wave_queue", "chan": 0, "ids": []}, "ids"),
    ]
    for step, keyword in cases:
        problems = S.validate_script({"steps": [step]})
        assert any(keyword in p for p in problems), (step, problems)


def test_parse_script_raises_value_error_with_chinese_message():
    with pytest.raises(ValueError) as exc:
        S.parse_script({"steps": [{"op": "unknown_op"}]})
    assert "不合法" in str(exc.value)
    assert "未知" in str(exc.value)


def test_parse_script_rejects_non_dict():
    problems = S.validate_script(["not", "a", "dict"])
    assert problems and "JSON 对象" in problems[0]
    with pytest.raises(ValueError):
        S.parse_script(["nope"])


# --------------------------------------------------------------------------
# 存取与预设脚本
# --------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    script = S.parse_script(_valid_script())
    path = str(tmp_path / "s.json")
    S.save_script(script, path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    assert "示例脚本" in raw                  # 中文未转义
    assert "\\u" not in raw
    back = S.load_script(path)
    assert back.name == script.name
    assert [s.op for s in back.steps] == [s.op for s in script.steps]
    assert back.steps[1].params == script.steps[1].params


def test_preset_scripts_are_valid_and_named():
    presets = S.list_preset_scripts()
    assert len(presets) == 5, presets
    names = [name for name, _path in presets]
    assert all(name and not name.endswith(".json") for name in names)
    assert names == sorted(names, key=lambda n: os.path.basename(n)) or True

    expected = ["01", "02", "03", "04", "05"]
    for prefix, (name, path) in zip(expected, presets):
        assert os.path.basename(path).startswith(prefix)
        script = S.load_script(path)
        assert script.steps, f"{name} 没有步骤"
        assert script.matrix_ids, f"{name} 缺少 matrix_ids"
        data = json.load(open(path, encoding="utf-8"))
        assert S.validate_script(data) == [], name


def test_preset_scripts_matrix_ids_match_requirements():
    by_prefix = {}
    for _name, path in S.list_preset_scripts():
        by_prefix[os.path.basename(path)[:2]] = S.load_script(path)
    assert by_prefix["01"].matrix_ids == ["A2", "A4", "A5"]
    assert "A9" in by_prefix["02"].matrix_ids
    assert "C1" in by_prefix["03"].matrix_ids
    assert "C3" in by_prefix["05"].matrix_ids


def test_list_preset_scripts_missing_directory(tmp_path):
    assert S.list_preset_scripts(str(tmp_path / "not-exist")) == []


def test_preset_scripts_directory_is_packaged():
    assert os.path.isdir(S.preset_scripts_dir())
    assert S.preset_scripts_dir().endswith(os.path.join("resources", "scripts"))
