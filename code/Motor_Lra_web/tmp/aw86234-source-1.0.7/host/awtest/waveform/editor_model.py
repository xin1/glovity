# -*- coding: utf-8 -*-
"""波形编辑文档模型（`05` 文档 §5.5 "波形编辑页" 的纯逻辑部分）。

:class:`WaveformDoc` 是一个带**撤销/重做栈**（默认 64 步，远大于要求的 20 步）
的样本容器，提供画布需要的全部编辑操作：手绘、幅度缩放、偏移、取反、
左右裁剪、区间静音、插值重采样。

约定：

* 所有样本都是**有符号**整数，统一裁剪到 ``-127..127``。
* 除 :meth:`WaveformDoc.set_samples` 外，所有会改变样本的方法都自动压一次撤销栈
  （可用 ``doc.auto_undo = False`` 临时关闭，例如连续拖动手绘时由界面自己在
  笔画开始时压栈，避免一步一个撤销点）。
* :func:`sram_budget` 实现 `05` 文档 §5.5 的 SRAM 预算公式 ``1 + 4N + Σlen``。

本模块**不依赖任何 GUI 库**，可单独 pytest。
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from ..protocol import commands as C
from .generator import SAMPLE_MAX, SAMPLE_MIN, clip

#: 撤销栈默认深度（要求 ≥20 步）
MAX_UNDO = 64

#: SRAM 总容量（协议 §9.8：共 3 kB）
SRAM_TOTAL_BYTES = C.SRAM_TOTAL_BYTES

#: 本板波形库起始地址（协议附录 B：``base_addr`` 默认 0x0800）
DEFAULT_BASE_ADDR = 0x0800


class WaveformDoc:
    """带撤销栈的波形文档模型。

    >>> doc = WaveformDoc([0, 10, 20, 30])
    >>> doc.scale(2.0)
    >>> doc.samples
    [0, 20, 40, 60]
    >>> doc.undo()
    True
    >>> doc.samples
    [0, 10, 20, 30]
    """

    def __init__(self, samples: Iterable[int] = ()) -> None:
        self.samples: List[int] = [clip(s) for s in samples]
        #: 是否每次修改都自动压撤销栈
        self.auto_undo: bool = True
        #: 撤销栈深度上限
        self.max_undo: int = MAX_UNDO
        self._undo: List[List[int]] = []
        self._redo: List[List[int]] = []

    # ------------------------------------------------------------------
    # 基本信息
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.samples)

    def __repr__(self) -> str:                      # pragma: no cover - 调试用
        return f"WaveformDoc(len={len(self.samples)}, peak={self.peak()})"

    def peak(self) -> int:
        """峰值（绝对值最大值）；空文档返回 0。"""
        return max((abs(int(s)) for s in self.samples), default=0)

    def duration_ms(self, sample_rate: int) -> float:
        """整段波形的时长（毫秒）。"""
        sr = int(sample_rate)
        if sr <= 0:
            raise ValueError("采样率必须为正数")
        return len(self.samples) * 1000.0 / sr

    # ------------------------------------------------------------------
    # 文档设置与撤销栈
    # ------------------------------------------------------------------

    def set_samples(self, samples: Iterable[int]) -> None:
        """整体替换样本（**不**压撤销栈，调用方需要时自己先 :meth:`push_undo`）。"""
        self.samples = [clip(s) for s in samples]

    def push_undo(self) -> None:
        """把当前状态压入撤销栈，并清空重做栈。"""
        self._undo.append(list(self.samples))
        if len(self._undo) > self.max_undo:
            del self._undo[0:len(self._undo) - self.max_undo]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        """撤销一步；没有可撤销的内容时返回 ``False``。"""
        if not self._undo:
            return False
        self._redo.append(list(self.samples))
        self.samples = self._undo.pop()
        return True

    def redo(self) -> bool:
        """重做一步；没有可重做的内容时返回 ``False``。"""
        if not self._redo:
            return False
        self._undo.append(list(self.samples))
        self.samples = self._redo.pop()
        return True

    def clear_history(self) -> None:
        """清空撤销/重做栈。"""
        self._undo.clear()
        self._redo.clear()

    def _apply(self, fn: Callable[[List[int]], List[int]]) -> None:
        """统一的修改入口：按需压栈后调用 ``fn`` 得到新样本。"""
        if self.auto_undo:
            self.push_undo()
        self.samples = [clip(v) for v in fn(list(self.samples))]

    # ------------------------------------------------------------------
    # 变换操作
    # ------------------------------------------------------------------

    def scale(self, factor: float) -> None:
        """幅度缩放（``factor`` 可以是小数；结果裁剪到 ±127）。"""
        self._apply(lambda src: [int(round(s * float(factor))) for s in src])

    def offset(self, delta: int) -> None:
        """整体偏移（叠加直流分量）。"""
        self._apply(lambda src: [s + int(delta) for s in src])

    def invert(self) -> None:
        """取反。"""
        self._apply(lambda src: [-s for s in src])

    def crop(self, start: int, end: int) -> None:
        """保留 ``[start, end)`` 区间的样本（下标越界自动收敛）。"""
        n = len(self.samples)
        lo = max(0, min(int(start), n))
        hi = max(0, min(int(end), n))
        if hi <= lo:
            raise ValueError(f"裁剪区间非法：start={start} end={end}（长度 {n}）")
        self._apply(lambda src: src[lo:hi])

    def mute(self, start: int, end: int) -> None:
        """把 ``[start, end)`` 区间置零（静音）。"""
        n = len(self.samples)
        lo = max(0, min(int(start), n))
        hi = max(0, min(int(end), n))

        def _mute(src: List[int]) -> List[int]:
            for i in range(lo, hi):
                src[i] = 0
            return src

        self._apply(_mute)

    def resample(self, target_len: int) -> None:
        """线性插值重采样到 ``target_len`` 个样本（保留首尾端点）。"""
        target = int(target_len)
        if target < 1:
            raise ValueError(f"目标样本数必须 ≥1，实际 {target_len}")
        src = list(self.samples)
        n = len(src)
        if n == 0:
            raise ValueError("空波形无法重采样")

        def _resample(_ignored: List[int]) -> List[int]:
            if target == n:
                return src
            if n == 1:
                return [src[0]] * target
            out: List[int] = []
            for i in range(target):
                if target == 1:
                    out.append(src[0])
                    continue
                pos = i * (n - 1) / float(target - 1)
                i0 = int(math.floor(pos))
                i1 = min(n - 1, i0 + 1)
                frac = pos - i0
                out.append(int(round(src[i0] * (1.0 - frac) + src[i1] * frac)))
            return out

        self._apply(_resample)

    def draw(self, index: int, value: int, brush: int = 0) -> None:
        """手绘：把 ``index`` 处（以及左右各 ``brush`` 个邻居）设为 ``value``。

        连续拖动画点时建议先 ``doc.auto_undo = False``，笔画开始时再手动
        :meth:`push_undo` 一次。
        """
        n = len(self.samples)
        idx = int(index)
        if n == 0 or idx < 0 or idx >= n:
            return
        lo = max(0, idx - max(0, int(brush)))
        hi = min(n - 1, idx + max(0, int(brush)))
        painted = clip(value)

        def _draw(src: List[int]) -> List[int]:
            for i in range(lo, hi + 1):
                src[i] = painted
            return src

        self._apply(_draw)


# --------------------------------------------------------------------------
# SRAM 预算
# --------------------------------------------------------------------------


def sram_budget(wave_count: int, total_samples: int,
                base_addr: int = DEFAULT_BASE_ADDR) -> Tuple[int, int, bool]:
    """波形库 SRAM 预算（`05` 文档 §5.5）。

    波形库布局（协议 §9.8 ``WAVE_COMMIT``）：``版本字节(1) + N×4 字节头 + 波形数据``，
    因此 ``used = 1 + 4N + Σlen``。

    可用字节数 = ``SRAM 总量(3072) − base_addr``。本板 ``base_addr = 0x0800``，
    即波形库可用 **1024 字节**（`0x800`~`0xBFF`）；`BASE_ADDR` 以下的 2 kB 留给 RTP FIFO
    （其默认阈值 ``FIFO_AE=512`` / ``FIFO_AF=1536`` 正好落在下方）。

    .. note::
       `02_AW8623X寄存器与模式参考.md` §5 有一句"以 BASE_ADDR = 0x0800 为例，波形库
       可用空间约 0x0800 字节（2 kB）"，与**同一节的组库算法**（``start = BASE_ADDR + offset``，
       向上生长）以及协议附录 D 第 12 条（"按波形库在 BASE_ADDR 之上理解"）矛盾。
       本实现以算法与附录 D 为准，详见 ``docs/协议一致性说明.md`` §2.2。
       固件 ``wave_lib_avail_bytes()`` 用的是同一个公式，两侧必须一致。

    .. note::
       固件 ``WAVE_BEGIN`` 应答里的 ``max_total_samples`` 才是权威上限
       （协议 §9.8），本函数用于界面**提前拦截**；两者口径一致，可互相印证。

    返回 ``(已用字节, 可用字节, 是否超限)``。
    """
    n = max(0, int(wave_count))
    total = max(0, int(total_samples))
    used = 1 + 4 * n + total
    base = int(base_addr)
    if not (0 <= base < SRAM_TOTAL_BYTES):
        base = DEFAULT_BASE_ADDR                  # base_addr 非法时回落到默认
    avail = SRAM_TOTAL_BYTES - base
    return used, avail, used > avail
