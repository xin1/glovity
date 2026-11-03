# -*- coding: utf-8 -*-
"""波形编辑页（规格书 §5.5）：生成器 / 手绘 / 变换 / CSV / 下发 / 回读校验 / 一键播放。

波形数据结构一律使用 ``awtest.waveform``（接口约定 §2）。该模块由波形引擎提供；
若此刻尚未就绪（``ImportError``），本页只给出明确提示并禁用相关按钮，绝不崩溃。

SRAM 预算用 ``editor_model.sram_budget``（``used = 1 + 4N + Σlen``）实时显示，
超限在本地就拦下来，不等固件回 ``ERR_SRAM_OVERFLOW``。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from awtest.protocol import codec as K
from awtest.protocol import commands as C

from . import (HAVE_WAVEFORM, BasePage,
               missing_module_hint)

try:  # pyqtgraph 是可选依赖：没有它时退化成一个提示标签
    import pyqtgraph as pg
    HAVE_PYQTGRAPH = True
except Exception as exc:  # pragma: no cover
    pg = None
    HAVE_PYQTGRAPH = False
    PYQTGRAPH_ERROR = f"{type(exc).__name__}: {exc}"
else:
    PYQTGRAPH_ERROR = ""

_SAMPLE_MIN, _SAMPLE_MAX = -128, 127
_RATE_CHOICES = ((C.RATE_12K, "12 kHz"), (C.RATE_24K, "24 kHz"),
                 (C.RATE_48K, "48 kHz（本板不可用）"))


class PageWaveform(BasePage):
    """波形库 + 编辑画布 + 下发/回读。"""

    title = "波形编辑"

    # ------------------------------------------------------------------

    def build(self) -> None:
        self.waves: Dict[int, List[int]] = {}
        self.current_id: int = 0
        self.doc = None
        self._loading = False
        self._draw_brush = True

        root = QVBoxLayout(self)
        if not HAVE_WAVEFORM:
            self.banner = QLabel(missing_module_hint("waveform"))
            self.banner.setWordWrap(True)
            self.banner.setStyleSheet(
                "background:#4a3b00; color:#ffd75f; padding:6px; border:1px solid #e6b422;")
            root.addWidget(self.banner)
        else:
            self.banner = None

        root.addWidget(self._build_top_group())

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_library_group())
        splitter.addWidget(self._build_canvas_group())
        splitter.addWidget(self._build_tools_group())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)

        root.addWidget(self._build_upload_group())

        if HAVE_WAVEFORM:
            self._new_wave()
        else:
            self._set_enabled(False)
        self._refresh_budget()

    # ------------------------------------------------------------------
    # 顶部：目标通道 / 采样率 / 预算条
    # ------------------------------------------------------------------

    def _build_top_group(self) -> QGroupBox:
        box = QGroupBox("目标与采样率")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("目标通道："))
        self.cmb_chan = QComboBox()
        for ch in range(C.CHANNEL_COUNT):
            self.cmb_chan.addItem(f"CH{ch}", ch)
        self.cmb_chan.setToolTip("波形下发的目标通道。默认跟随主窗口通道矩阵的第一路勾选。")
        row.addWidget(self.cmb_chan)

        row.addWidget(QLabel("采样率："))
        self.cmb_rate = QComboBox()
        for code, name in _RATE_CHOICES:
            self.cmb_rate.addItem(name, code)
        self.cmb_rate.setToolTip("WAVE_BEGIN / WAVE_GEN 的采样率编码。\n"
                                 "12 kHz = b10（默认）；24 kHz = b00（装库时带宽吃紧）；\n"
                                 "48 kHz = b01 需 ≥1 MHz I2C，本板不可用。")
        self.cmb_rate.setCurrentIndex(0)
        row.addWidget(self.cmb_rate)

        row.addWidget(QLabel("波形条数："))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, C.WAVE_MAX_WAVES)
        self.sp_count.setValue(1)
        self.sp_count.setToolTip("WAVE_BEGIN 的 wave_count，最多 127 条。\n"
                                 "SRAM 预算 = 1（版本字节）+ 4×N（每条 4 字节头）+ Σ样本数。")
        row.addWidget(self.sp_count)
        row.addStretch(1)
        root.addLayout(row)

        bl = QHBoxLayout()
        bl.addWidget(QLabel("SRAM 预算："))
        self.budget_bar = QProgressBar()
        self.budget_bar.setRange(0, 100)
        self.budget_bar.setFormat("%v / %m 字节")
        bl.addWidget(self.budget_bar, 1)
        self.budget_label = QLabel("")
        self.budget_label.setStyleSheet("font-family:Consolas,monospace;")
        bl.addWidget(self.budget_label)
        root.addLayout(bl)

        self.canvas_note = QLabel(
            "注意：USB-CDC 延迟大，画布上的播放头只能示意，不能当实时示波器使用。")
        self.canvas_note.setStyleSheet("color:#888;")
        self.canvas_note.setWordWrap(True)
        root.addWidget(self.canvas_note)
        return box

    # ------------------------------------------------------------------
    # 波形库
    # ------------------------------------------------------------------

    def _build_library_group(self) -> QGroupBox:
        box = QGroupBox("波形库（固件 SRAM 里的波形）")
        root = QVBoxLayout(box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["波形号", "样本数", "时长(ms)", "峰值", "校验"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(140)             # ★ 防止被滚动区压缩
        self.table.itemSelectionChanged.connect(self._on_lib_selected)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_new.clicked.connect(self._new_wave)
        self.btn_copy = QPushButton("复制")
        self.btn_copy.clicked.connect(self._copy_wave)
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(self._delete_wave)
        for b in (self.btn_new, self.btn_copy, self.btn_del):
            row.addWidget(b)
        root.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_readback = QPushButton("回读校验")
        self.btn_readback.setToolTip("WAVE_READBACK 逐段读回并与本地比对，不一致会高亮")
        self.btn_readback.clicked.connect(self._readback_verify)
        self.btn_erase = QPushButton("擦除整库")
        self.btn_erase.setToolTip("WAVE_ERASE（需要二次确认）")
        self.btn_erase.clicked.connect(self._erase)
        self.btn_library = QPushButton("读固件地址表")
        self.btn_library.setToolTip("WAVE_GET_LIBRARY：显示固件侧解析出的每个波形的起止地址")
        self.btn_library.clicked.connect(self._get_library)
        for b in (self.btn_readback, self.btn_erase, self.btn_library):
            row2.addWidget(b)
        root.addLayout(row2)
        return box

    # ------------------------------------------------------------------
    # 画布
    # ------------------------------------------------------------------

    def _build_canvas_group(self) -> QGroupBox:
        box = QGroupBox("编辑画布（横轴样本/ms，纵轴幅度 -128..127）")
        root = QVBoxLayout(box)
        if HAVE_PYQTGRAPH:
            self.plot = pg.PlotWidget()
            self.plot.setBackground("k")
            # ★ 最小高度：外层 QScrollArea(widgetResizable=True) 会把页面缩到视口大小，
            #   PlotWidget 的最小高度接近 0，不设下限就会被压成几十像素的细条。
            self.plot.setMinimumHeight(260)
            self.plot.showGrid(x=True, y=True, alpha=0.3)
            self.plot.setLabel("left", "幅度", units="")
            self.plot.setLabel("bottom", "样本序号")
            self.plot.setYRange(_SAMPLE_MIN - 8, _SAMPLE_MAX + 8)
            self.curve = self.plot.plot([], [], pen=pg.mkPen("#5fa8d3", width=2))
            self.playhead = pg.InfiniteLine(angle=90, movable=False,
                                            pen=pg.mkPen("#ff5555", style=Qt.DashLine))
            self.plot.addItem(self.playhead)
            self.playhead.setPos(0)
            self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
            root.addWidget(self.plot, 1)
        else:
            self.plot = None
            self.curve = None
            self.playhead = None
            lbl = QLabel("未安装 pyqtgraph，画布不可用（可继续用生成器/CSV/下发功能）：\n"
                         + PYQTGRAPH_ERROR)
            lbl.setStyleSheet("color:#ff5555;")
            lbl.setWordWrap(True)
            root.addWidget(lbl)

        row = QHBoxLayout()
        self.chk_draw = QCheckBox("手绘（拖动鼠标画点）")
        self.chk_draw.setToolTip("勾选后按住左键在画布上拖动即可改样本值；\n"
                                 "不勾选『画笔』时是橡皮（把样本拉向 0）。")
        row.addWidget(self.chk_draw)
        self.chk_eraser = QCheckBox("橡皮")
        row.addWidget(self.chk_eraser)
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self._redo)
        row.addWidget(self.btn_undo)
        row.addWidget(self.btn_redo)
        self.btn_preview = QPushButton("预览播放（示意）")
        self.btn_preview.setToolTip("在画布上移动播放头，仅示意；USB-CDC 延迟大，不是实时示波器。")
        self.btn_preview.clicked.connect(self._preview)
        row.addWidget(self.btn_preview)
        row.addStretch(1)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("font-family:Consolas,monospace; color:#5fa8d3;")
        row.addWidget(self.info_label)
        root.addLayout(row)
        return box

    # ------------------------------------------------------------------
    # 工具（生成器 / 变换 / CSV）
    # ------------------------------------------------------------------

    def _build_tools_group(self) -> QGroupBox:
        box = QGroupBox("生成器 / 变换 / CSV")
        outer = QVBoxLayout(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        root = QVBoxLayout(holder)

        # 生成器
        gen = QGroupBox("生成器")
        gf = QFormLayout(gen)
        self.cmb_shape = QComboBox()
        shapes = None
        if HAVE_WAVEFORM:
            from awtest.waveform import generator as _g
            shapes = _g.SHAPES
        if shapes is None:
            shapes = C.SHAPE_NAMES
        for code in sorted(shapes):
            self.cmb_shape.addItem(shapes[code], code)
        self.cmb_shape.setToolTip("WAVE_GEN 的 shape 编码：0=正弦 1=方波 2=三角 3=锯齿 "
                                  "4=正弦+上升包络 5=正弦+下降包络 6=点击。")
        gf.addRow("波形类型", self.cmb_shape)

        self.sp_freq = QDoubleSpinBox()
        self.sp_freq.setRange(1.0, 20000.0)
        self.sp_freq.setValue(175.0)
        self.sp_freq.setDecimals(1)
        self.sp_freq.setSuffix(" Hz")
        self.sp_freq.setToolTip("生成频率。一个周期的样本数 = 采样率 / 频率，必须是整数才不会相位跳变；\n"
                                "除不尽时会取最接近的整数周期样本数并显示实际频率。")
        self.sp_freq.valueChanged.connect(self._refresh_period_hint)
        gf.addRow("频率", self.sp_freq)

        self.sp_amp = QSpinBox()
        self.sp_amp.setRange(0, 127)
        self.sp_amp.setValue(100)
        self.sp_amp.setToolTip("幅度（0..127），对应有符号 8 位样本的峰值。")
        gf.addRow("幅度", self.sp_amp)

        self.sp_cycles = QSpinBox()
        self.sp_cycles.setRange(1, 4000)
        self.sp_cycles.setValue(1)
        self.sp_cycles.setToolTip("周期数：波形总样本数 = 周期样本数 × 周期数。")
        gf.addRow("周期数", self.sp_cycles)

        self.sp_phase = QDoubleSpinBox()
        self.sp_phase.setRange(-360.0, 360.0)
        self.sp_phase.setSuffix(" °")
        self.sp_phase.setToolTip("初相位（度）。")
        gf.addRow("相位", self.sp_phase)

        self.period_label = QLabel("")
        self.period_label.setStyleSheet("color:#5fa8d3;")
        self.period_label.setWordWrap(True)
        gf.addRow(self.period_label)

        self.btn_gen = QPushButton("生成到画布")
        self.btn_gen.clicked.connect(self._generate)
        gf.addRow(self.btn_gen)
        root.addWidget(gen)

        # 变换
        tr = QGroupBox("变换")
        tf = QFormLayout(tr)
        self.sp_scale = QDoubleSpinBox()
        self.sp_scale.setRange(0.0, 8.0)
        self.sp_scale.setValue(1.0)
        self.sp_scale.setSingleStep(0.1)
        tf.addRow("幅度缩放 ×", self.sp_scale)
        self.sp_offset = QSpinBox()
        self.sp_offset.setRange(-128, 127)
        tf.addRow("偏移", self.sp_offset)
        self.sp_crop_from = QSpinBox()
        self.sp_crop_from.setRange(0, 1000000)
        self.sp_crop_to = QSpinBox()
        self.sp_crop_to.setRange(0, 1000000)
        tf.addRow("裁剪起点", self.sp_crop_from)
        tf.addRow("裁剪终点", self.sp_crop_to)
        self.sp_resample = QSpinBox()
        self.sp_resample.setRange(2, 100000)
        self.sp_resample.setValue(256)
        tf.addRow("重采样到样本数", self.sp_resample)

        grid = QHBoxLayout()
        b_scale = QPushButton("缩放")
        b_scale.clicked.connect(lambda: self._transform("scale"))
        b_off = QPushButton("偏移")
        b_off.clicked.connect(lambda: self._transform("offset"))
        b_inv = QPushButton("取反")
        b_inv.clicked.connect(lambda: self._transform("invert"))
        for b in (b_scale, b_off, b_inv):
            grid.addWidget(b)
        tf.addRow(grid)
        grid2 = QHBoxLayout()
        b_crop = QPushButton("裁剪")
        b_crop.clicked.connect(lambda: self._transform("crop"))
        b_mute = QPushButton("静音区间")
        b_mute.clicked.connect(lambda: self._transform("mute"))
        b_res = QPushButton("重采样")
        b_res.clicked.connect(lambda: self._transform("resample"))
        for b in (b_crop, b_mute, b_res):
            grid2.addWidget(b)
        tf.addRow(grid2)
        root.addWidget(tr)

        # CSV
        csv_box = QGroupBox("CSV 导入导出")
        cf = QVBoxLayout(csv_box)
        self.btn_import = QPushButton("导入 CSV")
        self.btn_import.setToolTip("支持 1 列（幅度）或 2 列（时间, 幅度）；\n"
                                   "导入后如样本数不匹配会提示并可自动重采样。")
        self.btn_import.clicked.connect(self._import_csv)
        self.btn_export = QPushButton("导出当前画布")
        self.btn_export.clicked.connect(self._export_csv)
        cf.addWidget(self.btn_import)
        cf.addWidget(self.btn_export)
        root.addWidget(csv_box)
        root.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll)
        return box

    # ------------------------------------------------------------------
    # 下发 / 播放
    # ------------------------------------------------------------------

    def _build_upload_group(self) -> QGroupBox:
        box = QGroupBox("下发到设备（WAVE_BEGIN → WAVE_ADD ≤184 样本/片 → WAVE_COMMIT）")
        root = QVBoxLayout(box)
        row = QHBoxLayout()
        self.btn_upload = QPushButton("下发波形库")
        self.btn_upload.setStyleSheet("font-weight:bold;")
        self.btn_upload.clicked.connect(self._upload)
        self.chk_verify = QCheckBox("下发后自动回读校验")
        self.chk_verify.setChecked(True)
        self.btn_play = QPushButton("一键播放（WAVE_QUEUE + SET_MODE(RAM) + PLAY）")
        self.btn_play.clicked.connect(self._play)
        self.btn_abort = QPushButton("中止会话")
        self.btn_abort.setToolTip("WAVE_ABORT：放弃当前下载会话")
        self.btn_abort.clicked.connect(self._abort)
        row.addWidget(self.btn_upload)
        row.addWidget(self.chk_verify)
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_abort)
        row.addStretch(1)
        root.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        self.upload_label = QLabel("尚未下发。")
        self.upload_label.setWordWrap(True)
        self.upload_label.setStyleSheet("color:#888;")
        root.addWidget(self.upload_label)
        self.mismatch_label = QLabel("")
        self.mismatch_label.setWordWrap(True)
        root.addWidget(self.mismatch_label)
        return box

    # ==================================================================
    # 波形库操作
    # ==================================================================

    def _set_enabled(self, on: bool) -> None:
        for w in (self.btn_new, self.btn_copy, self.btn_del, self.btn_readback,
                  self.btn_erase, self.btn_library, self.btn_gen, self.btn_upload,
                  self.btn_play, self.btn_abort, self.btn_import, self.btn_export,
                  self.btn_preview, self.btn_undo, self.btn_redo, self.sp_count,
                  self.cmb_shape, self.sp_freq, self.sp_amp, self.sp_cycles,
                  self.sp_phase, self.cmb_rate):
            w.setEnabled(on)
        if not on:
            self.upload_label.setText(missing_module_hint("waveform"))
            self.upload_label.setStyleSheet("color:#e6b422;")

    def _next_id(self) -> int:
        for i in range(1, C.WAVE_MAX_WAVES + 1):
            if i not in self.waves:
                return i
        return 0

    def _new_wave(self) -> None:
        if not HAVE_WAVEFORM:
            return
        new_id = self._next_id()
        if not new_id:
            self.ctx.warn(self, "波形条数已满", f"最多 {C.WAVE_MAX_WAVES} 条波形。")
            return
        from awtest.waveform import editor_model as em
        self.waves[new_id] = [0] * 64
        self.current_id = new_id
        self.doc = em.WaveformDoc(self.waves[new_id])
        self._refresh_library()
        self._refresh_canvas()
        self._select_row(new_id)

    def _copy_wave(self) -> None:
        if not HAVE_WAVEFORM or self.current_id == 0:
            return
        new_id = self._next_id()
        if not new_id:
            return
        self.waves[new_id] = list(self.waves[self.current_id])
        self._refresh_library()
        self.ctx.log(f"已复制波形 #{self.current_id} → #{new_id}", "user")

    def _delete_wave(self) -> None:
        if not HAVE_WAVEFORM or self.current_id == 0:
            return
        wid = self.current_id
        if not self.ctx.confirm(self, "删除波形", f"确认从本地库里删除波形 #{wid}？",
                                "（设备里的副本需要重新下发才会消失）"):
            return
        self.waves.pop(wid, None)
        self.current_id = next(iter(sorted(self.waves)), 0)
        if self.current_id:
            from awtest.waveform import editor_model as em
            self.doc = em.WaveformDoc(self.waves[self.current_id])
        else:
            self.doc = None
        self._refresh_library()
        self._refresh_canvas()

    def _select_row(self, wave_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == wave_id:
                self.table.selectRow(row)
                return

    def _on_lib_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return
        wid = item.data(Qt.UserRole)
        if wid == self.current_id or wid not in self.waves:
            return
        from awtest.waveform import editor_model as em
        self.current_id = wid
        self.doc = em.WaveformDoc(self.waves[wid])
        self._refresh_canvas()

    def _refresh_library(self) -> None:
        rate = self._sample_rate()
        self.table.setRowCount(0)
        for wid in sorted(self.waves):
            samples = self.waves[wid]
            row = self.table.rowCount()
            self.table.insertRow(row)
            ms = (len(samples) / rate * 1000.0) if rate else 0.0
            peak = max((abs(s) for s in samples), default=0)
            cells = [f"#{wid}", str(len(samples)), f"{ms:.1f}", str(peak), ""]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, wid)
                self.table.setItem(row, col, item)
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        if not HAVE_WAVEFORM:
            self.budget_label.setText("波形模块未就绪")
            self.budget_bar.setValue(0)
            return
        from awtest.waveform import editor_model as em
        total = sum(len(s) for s in self.waves.values())
        used, avail, over = em.sram_budget(len(self.waves), total, base_addr=0x0800)
        pct = int(min(100, used * 100.0 / max(1, avail)))
        self.budget_bar.setValue(pct)
        self.budget_bar.setFormat(f"{used} / {avail} 字节 (%p%)")
        self.budget_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #ff5555; }" if over else
            "QProgressBar::chunk { background-color: #8fbf6f; }")
        self.budget_label.setText(
            f"1 + 4×{len(self.waves)} + Σ{total} 样本 = {used} 字节"
            + ("　★ 超限：请减少波形条数或样本数" if over else ""))

    # ==================================================================
    # 画布
    # ==================================================================

    def _sample_rate(self) -> int:
        return C.RATE_HZ.get(int(self.cmb_rate.currentData()), 24000)

    def _refresh_canvas(self) -> None:
        if self.curve is None or self.doc is None:
            self.info_label.setText("")
            return
        samples = list(self.doc.samples)
        self.curve.setData(list(range(len(samples))), samples)
        rate = self._sample_rate()
        dur = len(samples) / rate * 1000.0 if rate else 0.0
        self.info_label.setText(
            f"#{self.current_id} · {len(samples)} 样本 · {dur:.1f} ms · "
            f"峰值 {self.doc.peak()} · 撤销{'可' if self.doc.can_undo() else '不可'}/"
            f"重做{'可' if self.doc.can_redo() else '不可'}")
        self.sp_resample.setValue(max(2, len(samples)))
        self.sp_crop_from.setMaximum(max(0, len(samples) - 1))
        self.sp_crop_to.setMaximum(max(0, len(samples) - 1))
        self.sp_crop_to.setValue(max(0, len(samples) - 1))
        self.sp_count.setValue(max(1, min(C.WAVE_MAX_WAVES, len(self.waves))))

    def _commit_doc(self) -> None:
        if self.doc is None or not self.current_id:
            return
        self.waves[self.current_id] = list(self.doc.samples)
        self._refresh_library()
        self._refresh_canvas()

    def _on_mouse_moved(self, pos) -> None:
        if not self.chk_draw.isChecked() or self.doc is None or self.plot is None:
            return
        from PySide6.QtWidgets import QApplication
        if not (QApplication.mouseButtons() & Qt.LeftButton):
            return
        vb = self.plot.plotItem.vb
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        point = vb.mapSceneToView(pos)
        idx = int(round(point.x()))
        if idx < 0 or idx >= len(self.doc.samples):
            return
        value = 0 if self.chk_eraser.isChecked() else int(max(_SAMPLE_MIN, min(_SAMPLE_MAX, point.y())))
        self.doc.draw(idx, value, brush=3)
        self.waves[self.current_id] = list(self.doc.samples)
        self.curve.setData(list(range(len(self.doc.samples))), self.doc.samples)

    def _undo(self) -> None:
        if self.doc is not None and self.doc.undo():
            self._commit_doc()

    def _redo(self) -> None:
        if self.doc is not None and self.doc.redo():
            self._commit_doc()

    def _preview(self) -> None:
        if self.doc is None or self.playhead is None:
            return
        self.ctx.log("预览播放仅为画布示意：USB-CDC 延迟大，不能当实时示波器。", "info")
        n = len(self.doc.samples)
        if n == 0:
            return
        from PySide6.QtCore import QTimer
        self._preview_pos = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(40)

        def step() -> None:
            self._preview_pos = (self._preview_pos + max(1, n // 60)) % n
            self.playhead.setPos(self._preview_pos)
            if self._preview_pos == 0:
                self._preview_timer.stop()
        self._preview_timer.timeout.connect(step)
        self._preview_timer.start()

    # ==================================================================
    # 生成器 / 变换 / CSV
    # ==================================================================

    def _refresh_period_hint(self) -> None:
        if not HAVE_WAVEFORM:
            return
        from awtest.waveform import generator as gen
        rate = self._sample_rate()
        try:
            n, actual = gen.periods_for(self.sp_freq.value(), rate)
        except Exception as exc:
            self.period_label.setText(f"频率换算失败：{exc}")
            return
        text = f"一个周期 = {n} 样本 → 实际频率 {actual:.2f} Hz"
        if abs(actual - self.sp_freq.value()) > 0.01:
            text += "（除不尽，已取最接近的整数周期样本数，避免相位跳变）"
        self.period_label.setText(text)

    def _generate(self) -> None:
        if not HAVE_WAVEFORM:
            return
        from awtest.waveform import generator as gen
        rate = self._sample_rate()
        try:
            samples = gen.generate(self.cmb_shape.currentData(), self.sp_freq.value(),
                                   self.sp_amp.value(), self.sp_cycles.value(),
                                   rate, self.sp_phase.value())
        except Exception as exc:
            self.ctx.error(self, "生成失败", "波形生成失败。", str(exc))
            return
        if self.doc is None:
            self._new_wave()
        if self.doc is None:
            return
        self.doc.set_samples(samples)
        self._commit_doc()
        n, actual = gen.periods_for(self.sp_freq.value(), rate)
        self.ctx.log(f"生成波形：{self.cmb_shape.currentText()} {self.sp_freq.value():.1f} Hz "
                     f"→ {len(samples)} 样本（周期 {n}，实际 {actual:.2f} Hz）", "user")

    def _transform(self, kind: str) -> None:
        if self.doc is None:
            return
        try:
            if kind == "scale":
                self.doc.scale(self.sp_scale.value())
            elif kind == "offset":
                self.doc.offset(self.sp_offset.value())
            elif kind == "invert":
                self.doc.invert()
            elif kind == "crop":
                self.doc.crop(self.sp_crop_from.value(), self.sp_crop_to.value() + 1)
            elif kind == "mute":
                self.doc.mute(self.sp_crop_from.value(), self.sp_crop_to.value() + 1)
            elif kind == "resample":
                self.doc.resample(self.sp_resample.value())
        except Exception as exc:
            self.ctx.error(self, "变换失败", "波形变换失败。", str(exc))
            return
        self._commit_doc()
        self.ctx.log(f"波形变换：{kind}", "user")

    def _import_csv(self) -> None:
        if not HAVE_WAVEFORM:
            return
        from awtest.waveform import io_csv
        path, _ = QFileDialog.getOpenFileName(self, "导入波形 CSV", "", "CSV (*.csv);;全部 (*)")
        if not path:
            return
        try:
            samples = io_csv.load_csv(path)
        except Exception as exc:
            self.ctx.error(self, "导入失败", "CSV 解析失败。", str(exc))
            return
        if not samples:
            self.ctx.warn(self, "导入失败", "文件里没有可用的样本。")
            return
        peak = max(abs(s) for s in samples)
        note = (f"导入 {len(samples)} 个样本，峰值 {peak}。\n"
                "若峰值明显小于 127，可先用『幅度缩放』归一化。")
        if peak > 0 and (peak < 100 or peak > 128):
            note += f"\n建议缩放系数 ≈ {127 / peak:.3f}。"
        self.ctx.notify(self, "CSV 导入完成", "已导入到画布。", note)
        if self.doc is None:
            self._new_wave()
        if self.doc is not None:
            self.doc.set_samples(samples)
            self._commit_doc()
        self.ctx.log(f"CSV 导入：{path}（{len(samples)} 样本）", "user")

    def _export_csv(self) -> None:
        if self.doc is None or not HAVE_WAVEFORM:
            return
        from awtest.waveform import io_csv
        path, _ = QFileDialog.getSaveFileName(self, "导出波形 CSV",
                                              f"wave_{self.current_id}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            io_csv.save_csv(path, self.doc.samples, self._sample_rate())
            self.ctx.log(f"CSV 导出：{path}", "user")
        except Exception as exc:
            self.ctx.error(self, "导出失败", "写入 CSV 失败。", str(exc))

    # ==================================================================
    # 下发 / 回读 / 播放
    # ==================================================================

    def _check_budget(self) -> bool:
        from awtest.waveform import editor_model as em
        total = sum(len(s) for s in self.waves.values())
        used, avail, over = em.sram_budget(len(self.waves), total, base_addr=0x0800)
        if over:
            self.ctx.warn(self, "SRAM 预算超限",
                          f"波形库需要 {used} 字节，超过可用 {avail} 字节。",
                          "请减少波形条数或样本数；界面上提前拦住，避免固件回 "
                          "ERR_SRAM_OVERFLOW。")
            return False
        return True

    def _upload(self) -> None:
        if not HAVE_WAVEFORM:
            return
        if not self.ctx.require_device(self):
            return
        if not self.waves:
            self.ctx.warn(self, "没有波形", "请先生成或导入一条波形。")
            return
        if not self._check_budget():
            return
        from awtest.waveform import generator as gen
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        rate_code = int(self.cmb_rate.currentData())
        rate = C.RATE_HZ.get(rate_code, 24000)
        if rate_code == C.RATE_48K:
            self.ctx.warn(self, "48 kHz 不可用",
                          "本板 I2C 只到 400 kHz，48 kHz 波形下发需要 ≥1 MHz。",
                          "固件会返回 ERR_UNSUPPORTED。请选 12 kHz 或 24 kHz。")
            return
        payload: List[Tuple[int, bytes]] = []
        for wid in sorted(self.waves):
            payload.append((wid, gen.to_bytes(self.waves[wid])))
        total_samples = sum(len(b) for _i, b in payload)
        chunk_count = sum((len(b) + C.WAVE_MAX_CHUNK_SAMPLES - 1) // C.WAVE_MAX_CHUNK_SAMPLES
                          for _i, b in payload)
        if not self.ctx.confirm(self, "下发波形库",
                                f"确认把 {len(payload)} 条波形下发到 CH{ch}？",
                                f"采样率 {rate // 1000} kHz，样本总数 {total_samples}，"
                                f"分片约 {chunk_count} 次 WAVE_ADD（每片 ≤184 样本，"
                                f"2 s 超时）。\n下发期间该通道被会话独占，建议先停止播放。"):
            return
        self.progress.setValue(0)
        self.upload_label.setText("下发中…")
        self.mismatch_label.setText("")
        import time as _time
        state = {"last": _time.perf_counter(), "times": []}

        def job(progress):
            def cb(done: int, total: int) -> None:
                now = _time.perf_counter()
                dt = (now - state["last"]) * 1000.0
                state["last"] = now
                state["times"].append(dt)
                progress((done, total, dt))
            resp = dev.upload_waveform(ch, payload, rate_code, 1, cb)
            return resp

        def on_progress(payload_) -> None:
            done, total, dt = payload_
            pct = int(done * 100 / max(1, total))
            self.progress.setValue(pct)
            times = state["times"]
            avg = sum(times) / len(times) if times else 0.0
            self.upload_label.setText(
                f"下发中… {done}/{total} 样本（{pct}%）· 本片 {dt:.1f} ms · "
                f"平均 {avg:.1f} ms/片 · 已完成 {len(times)} 片")

        def done(resp: K.WaveCommitResp) -> None:
            self.progress.setValue(100)
            entries = "、".join(f"#{e.wave_id}[{e.start}-{e.end}]" for e in resp.entries)
            self.upload_label.setText(
                f"下发完成：result={resp.result} total_bytes={resp.total_bytes} "
                f"lib_crc16=0x{resp.lib_crc16:04X} base=0x{resp.base_addr:04X}\n"
                f"固件地址表：{entries}")
            times = state["times"]
            if times:
                self.ctx.log(f"WAVE_COMMIT 成功：{len(times)} 片，"
                             f"平均 {sum(times) / len(times):.1f} ms/片，"
                             f"最慢 {max(times):.1f} ms", "info")
            if self.chk_verify.isChecked():
                self._readback_verify()

        self.ctx.log(f"开始下发波形库到 CH{ch}（{len(payload)} 条 / {total_samples} 样本）", "user")
        self.ctx.task(job, on_done=done, on_progress=on_progress, name="WAVE_COMMIT")

    def _readback_verify(self) -> None:
        if not HAVE_WAVEFORM or not self.waves:
            return
        if not self.ctx.require_device(self):
            return
        from awtest.waveform import generator as gen
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        payload = [(wid, gen.to_bytes(self.waves[wid])) for wid in sorted(self.waves)]

        def job(progress):
            return dev.verify_waveform(ch, payload)

        def done(bad: List[Tuple[int, int, int, int]]) -> None:
            if not bad:
                self.mismatch_label.setText("回读校验：全部样本与本地一致 ✔")
                self.mismatch_label.setStyleSheet("color:#8fbf6f;")
                self.ctx.log("WAVE_READBACK 回读校验通过（无差异）", "info")
                for row in range(self.table.rowCount()):
                    self.table.setItem(row, 4, QTableWidgetItem("✔"))
                return
            head = "、".join(f"#{wid}@{off} 本地{local}≠设备{got}"
                             for wid, off, local, got in bad[:8])
            self.mismatch_label.setText(
                f"回读校验：{len(bad)} 处不一致（前几处：{head}）—— 已高亮，建议重新下发")
            self.mismatch_label.setStyleSheet("color:#ff5555;")
            bad_ids = {wid for wid, _o, _l, _g in bad}
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item is not None and item.data(Qt.UserRole) in bad_ids:
                    cell = QTableWidgetItem("✘ 不一致")
                    cell.setForeground(Qt.GlobalColor.white)
                    cell.setBackground(Qt.GlobalColor.darkRed)
                    self.table.setItem(row, 4, cell)
            self.ctx.log(f"WAVE_READBACK 校验发现 {len(bad)} 处不一致", "warn")

        self.ctx.task(job, on_done=done, name="WAVE_READBACK")

    def _play(self) -> None:
        if not HAVE_WAVEFORM or not self.ctx.require_device(self):
            return
        if not self.waves:
            self.ctx.warn(self, "没有波形", "请先生成或导入一条波形。")
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())
        ids = sorted(self.waves)[:8]
        mask = 1 << ch

        def job(progress):
            allowed, why = dev.safety_check(mask)
            if not allowed:
                raise RuntimeError(why)
            dev.wave_queue(ch, ids)
            dev.set_mode(ch, C.MODE_RAM)
            return dev.play(ch, timeout_ms=0)

        def done(resp: K.PlayResp) -> None:
            self.ctx.playing_changed.emit(ch)
            self.ctx.log(f"一键播放：CH{ch} 队列 {ids}，实际安全超时 "
                         f"{resp.actual_timeout_ms} ms", "user")

        def failed(msg: str) -> None:
            self.ctx.warn(self, "播放未启动", "一键播放失败。", msg.splitlines()[0])

        self.ctx.log(f"一键播放：WAVE_QUEUE({ids}) + SET_MODE(RAM) + PLAY(CH{ch})", "user")
        self.ctx.task(job, on_done=done, on_error=failed, name="一键播放")

    def _abort(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())

        def job(progress):
            dev.wave_abort(ch)

        self.ctx.task(job, on_done=lambda _r: self.ctx.log(f"已中止 CH{ch} 的波形下载会话",
                                                           "user"), name="WAVE_ABORT")

    def _erase(self) -> None:
        if not self.ctx.require_device(self):
            return
        ch = int(self.cmb_chan.currentData())
        if not self.ctx.confirm(self, "擦除波形库",
                                f"确认擦除 CH{ch} 的整个 SRAM 波形库？",
                                "设备里的波形会全部丢失，需要重新下发。", danger=True):
            return
        dev = self.ctx.device

        def job(progress):
            dev.wave_erase(ch)

        self.ctx.task(job, on_done=lambda _r: self.ctx.log(f"CH{ch} 波形库已擦除", "user"),
                      name="WAVE_ERASE")

    def _get_library(self) -> None:
        if not self.ctx.require_device(self):
            return
        dev = self.ctx.device
        ch = int(self.cmb_chan.currentData())

        def job(progress):
            return dev.wave_get_library(ch)

        def done(lib: K.WaveLibrary) -> None:
            lines = [f"#{e.wave_id}: {e.start:#06x} ~ {e.end:#06x}（{e.length} 样本）"
                     for e in lib.entries]
            self.upload_label.setText(
                f"固件波形库：lib_version={lib.lib_version} total_bytes={lib.total_bytes} "
                f"base=0x{lib.base_addr:04X}\n" + ("；".join(lines) or "（空库）"))
            self.ctx.log(f"WAVE_GET_LIBRARY：{lib.wave_count} 条波形，"
                         f"{lib.total_bytes} 字节", "info")

        self.ctx.task(job, on_done=done, name="WAVE_GET_LIBRARY")

    # ==================================================================
    # 状态联动
    # ==================================================================

    def on_connection_changed(self, ok: bool) -> None:
        if not HAVE_WAVEFORM:
            self._set_enabled(False)
            return
        self._set_enabled(True)
        for w in (self.btn_upload, self.btn_play, self.btn_abort, self.btn_readback,
                  self.btn_erase, self.btn_library):
            w.setEnabled(ok)
        # 这些是"本地编辑"按钮，不依赖设备连接 → 始终可用。
        # 注意：这里必须是**属性名字符串**（下面用 getattr 取），不能直接传控件对象。
        for name in ("btn_new", "btn_copy", "btn_del", "btn_gen",
                     "btn_import", "btn_export", "btn_preview",
                     "btn_undo", "btn_redo"):
            w = getattr(self, name, None)
            if w is not None:
                w.setEnabled(True)

    def on_caps_changed(self, caps) -> None:
        """能力驱动：CAP_RTP_48K=0 / CAP_WAVE_DOWNLOAD=0 时禁用并写明原因。"""
        model = self.cmb_rate.model()
        for row in range(self.cmb_rate.count()):
            code = self.cmb_rate.itemData(row)
            if code != C.RATE_48K:
                continue
            item = model.item(row)
            if item is None:
                continue
            ok = self.ctx.cap(C.CAP_RTP_48K)
            item.setEnabled(ok)
            item.setToolTip("" if ok else
                            "不可用（CAP_RTP_48K=0）：经 TCA9548A 与 ESP32-C6 都只到 "
                            "400 kHz，48K 需 ≥1 MHz；固件会回 ERR_UNSUPPORTED。")
        if not self.ctx.cap(C.CAP_WAVE_DOWNLOAD):
            self.btn_upload.setEnabled(False)
            self.btn_upload.setToolTip("不可用（CAP_WAVE_DOWNLOAD=0）：固件未提供 WAVE_* 波形下发。")

    def on_selection_changed(self, mask: int) -> None:
        chans = self.ctx.selected_channels()
        if chans:
            idx = self.cmb_chan.findData(chans[0])
            if idx >= 0:
                self.cmb_chan.setCurrentIndex(idx)


__all__ = ["PageWaveform"]
