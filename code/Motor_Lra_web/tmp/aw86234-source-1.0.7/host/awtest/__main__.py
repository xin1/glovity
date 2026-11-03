# -*- coding: utf-8 -*-
"""``python -m awtest`` 入口。

用法::

    python -m awtest                  # 启动 GUI（默认）
    python -m awtest --port COM7      # 启动 GUI 并自动连接 COM7
    python -m awtest --cli --port COM7  # 无界面命令行模式（调试/脚本化用）
    python -m awtest --selfcheck      # 自检：依赖、协议向量、资源、GUI 可导入性、无 GUI 依赖
    python -m awtest --list-ports     # 只列出串口

``--selfcheck`` 在**没有串口、没有显示器**的机器上也能跑，并打印逐项结果。
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.dirname(HERE)
REPO_DIR = os.path.dirname(HOST_DIR)


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------


def _ok(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f" —— {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" —— {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" —— {detail}" if detail else ""))


def _check_gui_free_modules() -> bool:
    """硬性要求：protocol/ waveform/ script/ 不得 **import** 任何 GUI 模块。

    用 ``ast`` 解析语法树，只看真正的 import 语句——文档字符串里出现
    "PySide6" 这类字样（例如"本包不得 import PySide6"）不算违规。
    """
    import ast

    banned_roots = {"PySide6", "PyQt5", "PyQt6", "pyqtgraph"}
    bad: List[str] = []
    for pkg in ("protocol", "waveform", "script"):
        root = os.path.join(HERE, pkg)
        for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
            rel = os.path.relpath(path, HOST_DIR)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
            except SyntaxError as exc:
                bad.append(f"{rel}: 语法错误 {exc}")
                continue
            for node in ast.walk(tree):
                names: List[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        names = [node.module]
                    elif node.level and node.module:
                        names = [node.module]
                for name in names:
                    head = name.split(".")[0]
                    if head in banned_roots:
                        bad.append(f"{rel}:{node.lineno} import {name}")
                    if name.startswith("awtest.ui") or name == "ui" and pkg != "ui":
                        bad.append(f"{rel}:{node.lineno} import {name}")
    if bad:
        _fail("protocol/ waveform/ script/ 无 GUI 依赖", "；".join(bad))
        return False
    _ok("protocol/ waveform/ script/ 无 GUI 依赖（语法树里没有任何 GUI import）")
    return True


def _check_protocol() -> bool:
    from awtest.protocol.framing import (Frame, FrameParser, crc16_ccitt,
                                         frames_to_stream)
    from awtest.protocol import commands as C

    ok = True
    crc = crc16_ccitt(b"123456789")
    if crc == 0x29B1:
        _ok("CRC16-CCITT-FALSE 自检", '"123456789" → 0x29B1')
    else:
        _fail("CRC16-CCITT-FALSE 自检", f"得到 0x{crc:04X}，期望 0x29B1")
        ok = False

    vector_path = os.path.join(REPO_DIR, "reference", "protocol_test_vectors.json")
    if not os.path.exists(vector_path):
        _warn("协议测试向量", f"未找到 {vector_path}（跳过）")
        return ok
    import json
    try:
        with open(vector_path, "r", encoding="utf-8") as fh:
            vectors = json.load(fh)
    except Exception as exc:
        _fail("协议测试向量", f"解析失败：{exc}")
        return False
    items = vectors.get("vectors", []) if isinstance(vectors, dict) else vectors
    passed = 0
    failed: List[str] = []
    for i, vec in enumerate(items):
        raw = bytes.fromhex(vec.get("frame_hex", ""))
        try:
            if len(raw) != vec.get("frame_len", len(raw)):
                raise ValueError("长度与 frame_len 不符")
            if int.from_bytes(raw[-2:], "little") != vec.get("crc16"):
                raise ValueError("CRC 与向量不符")
            frame = Frame.parse(raw)
            if (frame.flags != vec["flags"] or frame.seq != vec["seq"]
                    or frame.cmd != vec["cmd"] or frame.chan != vec["chan"]
                    or frame.payload.hex().upper() != vec["payload_hex"]):
                raise ValueError("字段与向量不一致")
            if frame.to_bytes() != raw:
                raise ValueError("重新组帧不一致")
        except Exception as exc:
            failed.append(f"#{i} {exc}")
            continue
        parsed = FrameParser().feed(raw)
        if len(parsed) != 1:
            failed.append(f"#{i} 解析器产出 {len(parsed)} 帧")
            continue
        passed += 1
    if failed:
        _fail(f"协议测试向量（{passed}/{len(items)} 通过）", "；".join(failed))
        ok = False
    else:
        _ok(f"协议测试向量（{passed}/{len(items)} 通过）")

    # 粘包 / 逐字节断包
    frames = [Frame.request(C.CMD_PING, b"", seq=1),
              Frame.response(C.CMD_PING, bytes([1, 1, 2, 3, 1]) + (1234).to_bytes(4, "little"),
                             seq=1)]
    stream = frames_to_stream(frames)
    if len(FrameParser().feed(stream)) == 2:
        _ok("解析器：粘包（一次喂 2 帧）")
    else:
        _fail("解析器：粘包")
        ok = False
    parser = FrameParser()
    out = []
    for i in range(len(stream)):
        out.extend(parser.feed(stream[i:i + 1]))
    if len(out) == 2:
        _ok("解析器：逐字节断包")
    else:
        _fail("解析器：逐字节断包", f"产出 {len(out)} 帧")
        ok = False
    return ok


def _check_resources() -> bool:
    from awtest.protocol import commands as C
    from awtest import resources as RES

    ok = True
    book = RES.load_register_book()
    regs = book.get("registers", [])
    if not regs:
        _fail("寄存器地址簿", f"无法读取 {RES.REGISTER_BOOK_PATH}")
        return False
    mapping = RES.register_map()
    undefined = set(C.UNDEFINED_REGISTERS)
    overlap = sorted(a for a in mapping if a in undefined)
    missing = [hex(a) for a in range(0x00, 0x73)
               if a not in mapping and a not in undefined]
    if overlap or missing:
        _fail("寄存器地址簿覆盖", f"与未定义清单冲突 {overlap}；缺定义地址 {missing}")
        ok = False
    else:
        _ok("寄存器地址簿", f"{len(mapping)} 个地址，覆盖 0x00~0x72 全部已定义地址，"
                            f"未定义清单 {len(undefined)} 个")
    if C.CAPS_EXPECTED_BOARD_A & C.CAP_RTP_48K:
        _fail("本板能力位期望值", "错误地认为 RTP 48K 可用")
        ok = False
    else:
        _ok("本板能力位期望值", "RTP 48K / 电机电源 GPIO / RSTN GPIO 均为 0（界面会置灰并写明原因）")
    return ok


def _check_optional_modules() -> None:
    try:
        from awtest.waveform import editor_model, generator, io_csv  # noqa: F401
        _ok("可选模块 awtest.waveform", "生成器 / 文档模型 / CSV 均可用")
    except Exception as exc:
        _warn("可选模块 awtest.waveform 尚未就绪", f"{type(exc).__name__}: {exc}"
              "（波形编辑页会优雅降级）")
    try:
        from awtest.script import engine, report, schema  # noqa: F401
        _ok("可选模块 awtest.script", "脚本 schema / engine / report 均可用")
    except Exception as exc:
        _warn("可选模块 awtest.script 尚未完全就绪", f"{type(exc).__name__}: {exc}"
              "（测试脚本页会优雅降级）")


def selfcheck() -> int:
    print("=" * 72)
    print("awtest 自检（无需串口 / 无需显示器）")
    print("=" * 72)
    critical_ok = True
    _ok("Python", sys.version.split()[0] + f" ({sys.platform})")

    print("\n-- 运行期依赖 --")
    for name, label in (("PySide6", "PySide6 (Qt6)"), ("pyqtgraph", "pyqtgraph"),
                        ("serial", "pyserial"), ("numpy", "numpy")):
        try:
            mod = __import__(name)
            version = getattr(mod, "__version__", "?")
            _ok(label, f"版本 {version}")
        except Exception as exc:
            _fail(label, f"{type(exc).__name__}: {exc}")
            critical_ok = False
    try:
        import pytest  # noqa: F401
        _ok("pytest", "开发依赖可用")
    except Exception as exc:
        _warn("pytest", f"未安装：{exc}")

    print("\n-- 可选模块（并行开发） --")
    _check_optional_modules()

    print("\n-- 协议层 --")
    critical_ok &= _check_protocol()

    print("\n-- 资源与能力位 --")
    critical_ok &= _check_resources()

    print("\n-- 分层约束 --")
    critical_ok &= _check_gui_free_modules()

    print("\n-- 界面可导入性 --")
    try:
        from awtest.ui.main_window import MainWindow  # noqa: F401
        _ok("from awtest.ui.main_window import MainWindow")
    except Exception as exc:
        _fail("导入界面", f"{type(exc).__name__}: {exc}")
        critical_ok = False

    print("\n-- 串口枚举（无串口属正常） --")
    try:
        from awtest.protocol.transport import HAVE_SERIAL, list_ports
        ports = list_ports() if HAVE_SERIAL else []
        if ports:
            _ok("串口枚举", "；".join(p.label() for p in ports))
        else:
            _warn("串口枚举", "未发现串口（无硬件环境下正常）")
    except Exception as exc:
        _warn("串口枚举", f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 72)
    print("自检结论：" + ("全部关键项通过 ✔" if critical_ok else "存在失败项 ✘"))
    print("=" * 72)
    return 0 if critical_ok else 1


# --------------------------------------------------------------------------
# 命令行模式
# --------------------------------------------------------------------------

_CLI_HELP = """可用命令：
  help                     显示本帮助
  ports                    列出串口
  connect <port>           连接设备
  disconnect               断开
  ping / caps / status / safety / info
  probe <ch|all>           探测通道（CHIPID）
  r <ch> <addr> [len]      读寄存器（hex，例如 r 0 45 4）
  w <ch> <addr> <val>      写单字节（hex）
  dump <ch> <start> <len>  读一段寄存器（hex 参数）
  softrst <ch>             软复位
  default <ch>             恢复默认 Profile
  mode <ch> <ram|rtp|cont|trig>
  play <ch> [timeout_ms]   播放
  stop <ch|all>            停止
  park                     TRIG 静默
  trig <mask> <width_us> [count] [period_ms]
  f0 <ch>                  F0 检测
  det <ch> <seq>           DET_READ（seq: 0=VBAT 3=RL 4=OS）
  snapshot <ch>            快照
  cont <ch> <f0_hz> <lvl>  快速配 CONT 并播放
  quit / exit              退出
"""


def _print(obj) -> None:
    if hasattr(obj, "__dataclass_fields__"):
        for field in obj.__dataclass_fields__:
            print(f"  {field} = {getattr(obj, field)}")
    else:
        print(f"  {obj}")


def run_cli(port: Optional[str], baud: int = 115200) -> int:
    from awtest.device import AWTestDevice
    from awtest.protocol import commands as C
    from awtest.protocol.transport import DeviceError, TransportError

    dev = AWTestDevice()
    print("awtest 命令行模式。输入 help 查看命令；quit 退出。")
    if port:
        try:
            info = dev.connect(port, baud, on_step=lambda m: print(f"  [连接] {m}"))
            print("已连接：" + info.summary())
        except Exception as exc:
            print(f"连接失败：{exc}")
            return 1

    def need_port() -> bool:
        if not dev.is_open:
            print("尚未连接。请先 `connect <port>` 或带 --port 启动。")
            return False
        return True

    while True:
        try:
            line = input("awtest> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(_CLI_HELP)
            elif cmd == "ports":
                for p in AWTestDevice.list_ports():
                    print("  " + p.label())
            elif cmd == "connect":
                if not args:
                    print("用法：connect <port>")
                    continue
                info = dev.connect(args[0], baud, on_step=lambda m: print(f"  [连接] {m}"))
                print("已连接：" + info.summary())
            elif cmd == "disconnect":
                dev.disconnect()
                print("已断开（已尽力发送停止指令）")
            elif cmd == "ping":
                if need_port():
                    _print(dev.ping())
            elif cmd == "caps":
                if need_port():
                    caps = dev.get_caps()
                    _print(caps)
                    print("  能力：" + C.caps_summary(caps.caps))
            elif cmd == "status":
                if need_port():
                    _print(dev.get_status())
            elif cmd == "safety":
                if need_port():
                    _print(dev.get_safety())
            elif cmd == "info":
                for ch in range(8):
                    try:
                        _print(dev.ch_get_info(ch))
                    except Exception as exc:
                        print(f"  CH{ch}: {exc}")
            elif cmd == "probe":
                if need_port() and args:
                    if args[0] == "all":
                        res = dev.ch_probe_all()
                        print(f"  present_mask=0x{res.present_mask:02X}")
                        for chan, hid, lid, st in res.entries:
                            print(f"  CH{chan}: CHIPID=0x{(hid << 8) | lid:04X} status={st}")
                    else:
                        _print(dev.ch_probe(int(args[0], 0)))
            elif cmd == "r":
                if need_port():
                    ch, addr = int(args[0], 0), int(args[1], 0)
                    length = int(args[2], 0) if len(args) > 2 else 1
                    data = dev.reg_read(ch, addr, length)
                    print("  " + " ".join(f"{b:02X}" for b in data))
            elif cmd == "w":
                if need_port():
                    dev.reg_write(int(args[0], 0), int(args[1], 0), int(args[2], 0))
                    print("  已写入")
            elif cmd == "dump":
                if need_port():
                    res = dev.reg_dump(int(args[0], 0), int(args[1], 0), int(args[2], 0))
                    for i in range(0, len(res.data), 16):
                        row = res.data[i:i + 16]
                        print(f"  {res.start + i:02X}: " + " ".join(f"{b:02X}" for b in row))
            elif cmd == "softrst":
                if need_port():
                    dev.aw_softrst(int(args[0], 0))
                    print("  已软复位（固件会重新下发 Profile）")
            elif cmd == "default":
                if need_port():
                    dev.set_default_profile(int(args[0], 0))
                    print("  已恢复默认 Profile")
            elif cmd == "mode":
                if need_port():
                    dev.set_mode(int(args[0], 0), C.MODE_CODES[args[1].upper()])
                    print("  已设置模式 " + args[1].upper())
            elif cmd == "play":
                if need_port():
                    timeout = int(args[1]) if len(args) > 1 else 0
                    print(f"  actual_timeout={dev.play(int(args[0], 0), timeout).actual_timeout_ms} ms")
            elif cmd == "stop":
                if need_port():
                    if args and args[0] == "all":
                        print(f"  stopped_mask=0x{dev.stop_all(True):02X}")
                    else:
                        dev.stop(int(args[0], 0))
                        print("  已停止")
            elif cmd == "park":
                if need_port():
                    dev.trig_park()
                    print("  已 TRIG_PARK")
            elif cmd == "trig":
                if need_port():
                    mask = int(args[0], 0)
                    width = int(args[1]) if len(args) > 1 else 20
                    count = int(args[2]) if len(args) > 2 else 1
                    period = int(args[3]) if len(args) > 3 else 100
                    print(f"  started=0x{dev.trig_pulse(mask, width, count, period):02X}")
            elif cmd == "f0":
                if need_port():
                    res = dev.f0_measure(int(args[0], 0), 500, 0)
                    _print(res)
                    if not res.valid:
                        print("  " + res.diagnosis())
            elif cmd == "det":
                if need_port():
                    res = dev.det_read(int(args[0], 0), int(args[1], 0))
                    _print(res)
                    print(f"  VDD={res.vbat_v(res.avg_data):.3f} V  "
                          f"RL={res.rl_ohm(res.avg_data, dev.d2s_gain):.3f} Ω"
                          f"（D2S_GAIN=×{dev.d2s_gain}）")
            elif cmd == "snapshot":
                if need_port():
                    snap = dev.snapshot(int(args[0], 0))
                    _print(snap)
                    print(f"  GLB_STATE={snap.glb_state_name}")
            elif cmd == "cont":
                if need_port():
                    ch = int(args[0], 0)
                    f0 = float(args[1])
                    lvl = int(args[2])
                    prof = dev.get_profile(ch)
                    # get_profile() 忠实回读寄存器；芯片不在 TRIG 模式时，0x3A bit2 /
                    # 0x39 bit3 / 0x45 bit6:4 / bit0 停在本模式无意义的复位默认值
                    # （自检软复位后就是这种状态），直接回写会被 Profile 的范围校验拒绝。
                    # 这几个是"必须"值，本命令也不打算改 TRIG，写回前归位。
                    prof.trig1_brk = 1
                    prof.trig1_stop = 1
                    prof.trig1_mode = 3
                    prof.intn_pin = 0
                    prof.mode = C.MODE_CONT
                    prof.set_f0_hz(f0)
                    prof.drv1_lvl = lvl
                    dev.set_profile(ch, prof)
                    dev.set_mode(ch, C.MODE_CONT)
                    dev.play(ch, 1000)
                    print(f"  CH{ch} CONT {prof.f0_hz:.1f} Hz lvl={lvl} 已播放")
            else:
                print("未知命令，输入 help 查看。")
        except DeviceError as exc:
            print(f"设备错误：{exc}")
        except TransportError as exc:
            print(f"传输错误：{exc}")
        except Exception as exc:
            print(f"错误：{type(exc).__name__}: {exc}")

    if dev.is_open:
        dev.disconnect()
    return 0


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------


def run_gui(port: Optional[str], auto_connect: bool = True) -> int:
    from PySide6.QtWidgets import QApplication
    from awtest.device import AWTestDevice
    from awtest.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AW86234 上位机")
    device = AWTestDevice()
    window = MainWindow(device)
    window.show()
    if port:
        idx = window.cmb_port.findData(port)
        if idx < 0:
            window.cmb_port.addItem(port, port)
            idx = window.cmb_port.count() - 1
        window.cmb_port.setCurrentIndex(idx)
        if auto_connect:
            window.connect_device()
    return int(app.exec())


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m awtest",
        description="AW86234 八路振动测试板 PC 上位机（PySide6）")
    parser.add_argument("--cli", action="store_true", help="使用无界面命令行模式")
    parser.add_argument("--port", "-p", default=None, help="串口名，例如 COM7")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="波特率（USB-CDC 随意）")
    parser.add_argument("--selfcheck", action="store_true", help="自检并退出（无需串口/显示器）")
    parser.add_argument("--list-ports", action="store_true", help="列出串口后退出")
    parser.add_argument("--no-connect", action="store_true", help="GUI 启动时不要自动连接")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if args.list_ports:
        from awtest.protocol.transport import HAVE_SERIAL, list_ports
        ports = list_ports() if HAVE_SERIAL else []
        if not ports:
            print("未发现串口。")
            return 0
        for p in ports:
            print(p.label() + (f"  [{p.hwid}]" if p.hwid else ""))
        return 0
    if args.cli:
        return run_cli(args.port, args.baud)
    try:
        return run_gui(args.port, auto_connect=not args.no_connect)
    except Exception as exc:  # pragma: no cover - 启动期异常
        print(f"启动界面失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
