# -*- coding: utf-8 -*-
"""界面冒烟测试：**无显示器、无串口**也要能跑。

覆盖：

* 构造 ``QApplication`` 与主窗口 + 六个页面（不连接真设备），断言不抛异常；
* 通道矩阵 CH5/CH6/CH7 的 TRIG 联动（``CAP_SHARED_TRIG_567``）：勾一路 → 三路全勾且加锁，
  切到 CONT → 解除联动；
* 寄存器页命中 ``commands.UNDEFINED_REGISTERS`` 时给出黄色警告文案；
* 能力位驱动的界面行为（RTP 48K 置灰并写明原因、断电重启置灰并写明原因）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from awtest.protocol import commands as C  # noqa: E402


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """离屏环境下模态对话框会把测试挂死：默认把确认框当作『用户取消』，提示框静默。

    需要验证确认流程的用例可以在函数体内用 ``monkeypatch`` 再覆盖一次。
    """
    from awtest.ui import UiContext
    monkeypatch.setattr(UiContext, "confirm", staticmethod(lambda *a, **k: False))
    monkeypatch.setattr(UiContext, "warn", staticmethod(lambda *a, **k: None))
    # UiContext 的"信息提示框"叫 notify，不叫 info（见 ui/__init__.py 的注释）
    monkeypatch.setattr(UiContext, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(UiContext, "error", staticmethod(lambda *a, **k: None))


@pytest.fixture()
def window(qapp):
    from awtest.ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    yield win
    win.close()
    # 等待后台任务真正结束，避免工作线程在窗口销毁后才回投信号
    import time
    deadline = time.time() + 3.0
    while win.ctx.runner.busy and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


# --------------------------------------------------------------------------
# 主窗口与页面
# --------------------------------------------------------------------------


def test_main_window_builds(window):
    assert window.centralWidget() is not None
    assert window.matrix is not None
    assert "AW86234" in window.windowTitle()


def test_six_pages_exist(window):
    from awtest.ui.page_waveform import PageWaveform
    from awtest.ui.page_measure import PageMeasure
    from awtest.ui.page_register import PageRegister
    from awtest.ui.page_testscript import PageTestScript
    from awtest.ui.page_mode_params import PageModeParams
    from awtest.ui.page_console import PageConsole

    expected = {
        "mode_params": PageModeParams,
        "waveform": PageWaveform,
        "measure": PageMeasure,
        "register": PageRegister,
        "testscript": PageTestScript,
        "console": PageConsole,
    }
    for key, cls in expected.items():
        page = window.pages[key]
        assert isinstance(page, cls), key
        assert isinstance(page, QWidget)
        assert page.title, f"{key} 缺少中文标题"
    assert window.tabs.count() >= 6


def test_every_page_is_registered_as_tab(window):
    for index in range(window.tabs.count()):
        widget = window.tabs.widget(index)
        assert widget is not None
        assert window.tabs.tabText(index)


# --------------------------------------------------------------------------
# 小屏幕可用性：窗口可缩小 + 每页可滚动（否则底部内容"."看不到也滚不到"）
# --------------------------------------------------------------------------


def test_window_can_shrink_to_small_screen(window):
    """窗口最小尺寸必须够小。

    修复前实测 `minimumSizeHint = 986x1294`：各页面的内容最小高度累加到窗口上，
    1080p / 带缩放的屏幕上放不下，而 Qt 不允许窗口小于 minimumSizeHint，
    底部内容就被切掉且无法滚动到（用户反馈"看不到、打不开"）。
    包一层 QScrollArea 后应降到约 872x426。
    """
    mh = window.minimumSizeHint()
    assert mh.height() <= 700, f"窗口最小高度 {mh.height()} px 过大，小屏幕会切掉底部"
    assert mh.width() <= 1000, f"窗口最小宽度 {mh.width()} px 过大"
    # 必须能从 1360x768 这种常见笔记本分辨率再缩小
    window.resize(1200, 640)
    assert window.width() <= 1200 and window.height() <= 700


def test_every_tab_is_inside_a_scroll_area(window):
    """每个标签页都要包在滚动区里，保证任何窗口尺寸下内容都够得着。"""
    from PySide6.QtWidgets import QScrollArea
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        name = window.tabs.tabText(i)
        assert isinstance(w, QScrollArea), f"{name} 页没有包滚动区"
        assert w.widgetResizable(), f"{name} 页的滚动区没有 setWidgetResizable(True)"
        assert w.widget() is not None, f"{name} 页的滚动区是空的"


def test_pages_dict_still_points_at_real_pages(window):
    """包装滚动区后，`window.pages[...]` 必须仍指向页面本体（大量测试与代码依赖它）。"""
    from PySide6.QtWidgets import QScrollArea
    for key, page in window.pages.items():
        assert not isinstance(page, QScrollArea), f"{key} 被错误地换成了滚动区"
        # 页面本体的父级应是某个滚动区
        assert page.parent() is not None


def _settle(window, qapp) -> None:
    """强制让布局与几何更新落地（Qt 的布局是延迟生效的）。"""
    cw = window.centralWidget()
    if cw is not None and cw.layout() is not None:
        cw.layout().activate()
    if window.layout() is not None:
        window.layout().activate()
    for _ in range(3):
        qapp.processEvents()


def test_matrix_collapse_reclaims_space(window, qapp):
    """收起通道矩阵必须真正腾出垂直空间（小屏幕上这 190 px 很关键）。"""
    window.matrix.set_compact(False)  # Detailed matrix retains the original collapse contract.
    window.matrix.set_collapsed(False)
    _settle(window, qapp)
    expanded = window.matrix.height()
    window.matrix.set_collapsed(True)
    _settle(window, qapp)
    collapsed = window.matrix.height()
    assert window.matrix.is_collapsed()
    assert collapsed < expanded - 100, \
        f"收起后高度只从 {expanded} 变成 {collapsed}，没有真正腾出空间"


def test_matrix_collapse_keeps_toolbar_row_usable(window, qapp):
    """收起后工具条那一行仍在：全选/反选/在线探测/选中掩码都还能用。"""
    window.matrix.set_collapsed(True)
    assert window.matrix.collapse_btn.isVisible()
    assert window.matrix.select_all_btn.isVisible()
    assert window.matrix.invert_btn.isVisible()
    assert window.matrix.probe_all_btn.isVisible()
    assert window.matrix.mask_label.isVisible()
    # 卡片本体应被隐藏
    assert not window.matrix.grid_holder.isVisible()
    window.matrix.set_collapsed(False)
    assert window.matrix.grid_holder.isVisible()


def test_matrix_collapse_state_persists(window, qapp):
    """收起状态应写进 QSettings，重启后保持。"""
    from PySide6.QtCore import QSettings
    window.matrix.set_collapsed(True)
    assert QSettings("Glovity", "AW86234-Testbench").value(
        "ui/matrix_collapsed", False, type=bool) is True
    window.matrix.set_collapsed(False)
    assert QSettings("Glovity", "AW86234-Testbench").value(
        "ui/matrix_collapsed", True, type=bool) is False


def test_scroll_area_does_not_squash_charts(window, qapp):
    """滚动区不能把图和表压扁。

    用户反馈过"图表加载不正确"：外层 QScrollArea(widgetResizable=True) 会把页面缩到
    视口大小，而 PlotWidget / QTableWidget 的最小高度接近 0，不给下限就会被布局压成
    几十像素的细条。这里验证在 1360×768（常见笔记本）下它们仍有可用高度。
    """
    from awtest.ui.page_measure import HAVE_PYQTGRAPH
    page = window.pages["measure"]
    window.resize(1360, 768)
    _settle(window, qapp)

    assert page.f0_table.minimumHeight() >= 80
    assert page.f0_table.height() >= 80, f"F0 表被压到 {page.f0_table.height()} px"
    assert page.snap_table.height() >= 120, f"快照表被压到 {page.snap_table.height()} px"
    assert page.rec_view.height() >= 100, f"记录框被压到 {page.rec_view.height()} px"
    if HAVE_PYQTGRAPH:
        assert page.f0_plot.minimumHeight() >= 150
        assert page.f0_plot.height() >= 150, f"F0 图被压到 {page.f0_plot.height()} px"
        assert page.mon_plot.height() >= 180, f"监视图被压到 {page.mon_plot.height()} px"


def test_other_pages_content_not_squashed(window, qapp):
    """其它页面同理：主要表格/画布必须有可用高度下限。"""
    window.resize(1360, 768)
    _settle(window, qapp)
    checks = [
        ("channels", "table", 180),
        ("waveform", "table", 120),
        ("register", "dump_table", 140),
        ("register", "watch_table", 100),
        ("testscript", "step_table", 140),
        ("testscript", "log_view", 100),
        ("console", "log_view", 140),
    ]
    for key, attr, least in checks:
        w = getattr(window.pages[key], attr, None)
        assert w is not None, f"{key}.{attr} 不存在"
        assert w.minimumHeight() >= least, \
            f"{key}.{attr} 的最小高度 {w.minimumHeight()} < {least}"


# --------------------------------------------------------------------------
# CH5/6/7 联动（规格书 §5.2）
# --------------------------------------------------------------------------


def test_channel_matrix_trig_linkage(window):
    matrix = window.matrix
    matrix.set_caps(C.CAP_SHARED_TRIG_567)

    # 非 TRIG 模式：三路独立
    matrix.set_trigger_group_mode(C.MODE_CONT)
    assert not matrix.trig_group_locked()
    assert all(matrix.card(ch).checkbox.isEnabled() for ch in (5, 6, 7))

    # 切到 TRIG：勾 CH5 → CH6/CH7 自动勾选且被禁用
    matrix.set_trigger_group_mode(C.MODE_TRIG)
    matrix.set_checked(5, True)
    for ch in (5, 6, 7):
        assert matrix.is_checked(ch), f"CH{ch} 应被自动勾选"
        assert matrix.is_locked(ch), f"CH{ch} 应加锁"
        assert not matrix.card(ch).checkbox.isEnabled(), f"CH{ch} 勾选框应被禁用"
    assert matrix.selected_mask() & 0xE0 == 0xE0

    # 加锁状态下无法取消勾选
    matrix.card(6).checkbox.setChecked(False)
    matrix._on_card_toggled(6, False)
    assert matrix.is_checked(6)


def test_channel_matrix_unlock_on_mode_switch(window):
    matrix = window.matrix
    matrix.set_caps(C.CAP_SHARED_TRIG_567)
    matrix.set_trigger_group_mode(C.MODE_TRIG)
    matrix.set_checked(6, True)
    assert matrix.is_locked(5) and matrix.is_locked(6) and matrix.is_locked(7)

    # 切到 RAM/CONT/RTP → 解除联动
    matrix.set_trigger_group_mode(C.MODE_CONT)
    for ch in (5, 6, 7):
        assert not matrix.is_locked(ch), f"CH{ch} 应解除联动"
        assert matrix.card(ch).checkbox.isEnabled(), f"CH{ch} 应恢复可勾选"
    assert not matrix.trig_group_locked()


def test_channel_matrix_without_cap_bit(window):
    """固件报 bit12=0 时不显示锁。"""
    matrix = window.matrix
    matrix.set_caps(0)
    matrix.set_trigger_group_mode(C.MODE_TRIG)
    matrix.set_checked(5, True)
    for ch in (5, 6, 7):
        assert not matrix.is_locked(ch)


# --------------------------------------------------------------------------
# 寄存器页：未定义地址警告
# --------------------------------------------------------------------------


def test_register_page_warns_undefined_address(window):
    page = window.pages["register"]
    undefined = sorted(C.UNDEFINED_REGISTERS)
    assert undefined, "未定义寄存器清单不应为空"
    addr = undefined[0]
    page.ed_addr.setText(f"0x{addr:02X}")
    page._on_addr_edit()
    text = page.addr_warn.text()
    assert text, "未定义地址应给出警告文案"
    assert "未定义" in text
    assert f"0x{addr:02X}" in text


def test_register_page_has_trap_hint(window):
    """0x45 / 0x33 / 0x3A 必须给"默认值陷阱"提示。"""
    page = window.pages["register"]
    for addr in (0x45, 0x33, 0x3A):
        page.ed_addr.setText(f"0x{addr:02X}")
        page._on_addr_edit()
        assert "陷阱" in page.addr_warn.text(), f"0x{addr:02X} 缺少陷阱提示"


def test_register_book_loaded(window):
    page = window.pages["register"]
    assert page.book, "寄存器地址簿应能加载"
    assert 0x45 in page.book
    assert page.book[0x45]["name"].startswith("SYSCTRL1")


# --------------------------------------------------------------------------
# 能力位驱动的界面（规格书 §5.3）
# --------------------------------------------------------------------------


def test_capability_driven_disabling(window):
    from awtest.protocol import codec as K

    caps = K.Caps(channel_count=8, max_payload=200,
                  caps=C.CAPS_EXPECTED_BOARD_A)   # 本板：bit10/bit11/bit13 都是 0
    window.ctx.set_caps(caps)
    window._apply_caps()

    # 断电重启置灰 + 写明原因
    conn = window.pages["connection"]
    assert not conn.btn_power_cycle.isEnabled()
    assert "SW3" in conn.btn_power_cycle.toolTip()
    assert not conn.btn_hw_reset.isEnabled()

    # RTP 48K 置灰 + 写明原因
    waveform = window.pages["waveform"]
    model = waveform.cmb_rate.model()
    for row in range(waveform.cmb_rate.count()):
        if waveform.cmb_rate.itemData(row) == C.RATE_48K:
            item = model.item(row)
            assert item is not None and not item.isEnabled()
            assert "400 kHz" in (item.toolTip() or "")


def test_internal_pullup_banner(window):
    from awtest.protocol import codec as K
    caps = K.Caps(8, 200, C.CAPS_EXPECTED_BOARD_A | C.CAP_INTERNAL_PULLUP)
    window.ctx.set_caps(caps)
    window._apply_caps()
    # 横幅由连接流程按 CAP_INTERNAL_PULLUP 打开
    window._set_banner("上游 I2C 上拉不足")
    assert window.banner.isVisible() or window.banner.text()


def test_amp_slider_disabled_outside_cont(window):
    slider = window.pages["mode_params"].amp_slider
    slider.set_context_channel(0, C.MODE_RAM, False)
    assert not slider.slider.isEnabled()
    slider.set_context_channel(0, C.MODE_CONT, True)
    assert slider.slider.isEnabled()


def test_console_page_accepts_logs(window):
    console = window.pages["console"]
    console.append("冒烟测试日志行", "info")
    console.append("冒烟测试错误行", "error")
    text = console.log_view.toPlainText()
    assert "冒烟测试日志行" in text
    assert "冒烟测试错误行" in text


def test_mode_params_roundtrip_without_device(window):
    from awtest import profiles as P
    page = window.pages["mode_params"]
    page._sync_ui(P.PRESET_FACTORIES["TRIG_上升沿"]())
    profile = page._sync_model()
    assert profile.mode == C.MODE_TRIG
    assert profile.trig1_brk == 1
    assert profile.trig1_stop == 1      # 只读且强制为开
    assert page.chk_brk.isEnabled() is False
    assert page.chk_stop.isEnabled() is False
    assert not profile.validate()


# --------------------------------------------------------------------------
# 异步路径（工作线程 + 设备桥）用假设备验证，仍然不需要真串口
# --------------------------------------------------------------------------


class _FakeStats:
    last_rtt_ms = 1.5
    rtt_avg_ms = 1.2


class _FakeTransport:
    """最小传输层替身：只记录 request 调用，不碰串口。"""

    def __init__(self) -> None:
        self.is_open = True
        self.binary_mode = True
        self.stats = _FakeStats()
        self.requests = []
        self.on_frame = None
        self.on_event = None
        self.on_ascii = None
        self.on_log = None
        self.on_disconnect = None

    def request(self, cmd, payload=b"", chan=0xFE, timeout=0.5):
        self.requests.append((cmd, payload, chan))

        class _Resp:
            def __init__(self):
                self.payload = b""
                self.cmd = cmd
                self.chan = chan
                self.is_error = False
                self.err_code_safe = 0
                self.error_text = ""

        return _Resp()

    def send_frame(self, cmd, payload=b"", chan=0xFE, noresp=False, seq=None):
        self.requests.append((cmd, payload, chan))
        return None

    def close(self, send_stop: bool = False) -> None:
        self.is_open = False


class _FakeDevice:
    """只实现界面用到的少量方法；用于验证『下发 → 回读 → 比对』与安全停止路径。"""

    def __init__(self, mismatches=None) -> None:
        from awtest import profiles as P
        self.t = _FakeTransport()
        self.profiles = {ch: P.Profile() for ch in range(8)}
        self.modes = {ch: C.MODE_RAM for ch in range(8)}
        self.playing = {ch: False for ch in range(8)}
        self.d2s_gain = 8
        self.info = None
        self.mismatches = list(mismatches or [])
        self.verified = []

    @property
    def is_open(self) -> bool:
        return self.t.is_open

    @property
    def stats(self):
        return self.t.stats

    def set_profile_verify(self, chan, profile):
        self.verified.append(chan)
        self.profiles[chan] = profile.copy()
        return list(self.mismatches)

    def get_profile(self, chan):
        return self.profiles[chan]

    def set_profile(self, chan, profile):
        self.profiles[chan] = profile.copy()

    def stop_all(self, immediate=True) -> int:
        return 0xFF

    def trig_park(self) -> None:
        return None

    def safety_check(self, mask, override=False):
        return True, ""


def _wait_for(qapp, predicate, timeout: float = 5.0) -> bool:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


def test_send_profile_verifies_and_reports_mismatch(window, qapp, monkeypatch):
    """下发 → 回读 → 比对：不一致项要进红色表格。"""
    from awtest.ui import UiContext

    monkeypatch.setattr(UiContext, "confirm", staticmethod(lambda *a, **k: True))
    fake = _FakeDevice(mismatches=[("drv1_lvl", 127, 96)])
    window.ctx.device = fake
    window.ctx.set_selection(0x03)          # CH0 + CH1

    page = window.pages["mode_params"]
    page._send()
    assert _wait_for(qapp, lambda: page.mismatch.rowCount() >= 1), "未收到比对结果"
    assert fake.verified == [0, 1]
    row_text = [page.mismatch.item(0, c).text() for c in range(5)]
    assert row_text[0] == "CH0"
    assert "127" in row_text[2] and "96" in row_text[3]
    assert "不一致" in page.result_label.text()


def test_send_profile_all_match(window, qapp, monkeypatch):
    from awtest.ui import UiContext

    monkeypatch.setattr(UiContext, "confirm", staticmethod(lambda *a, **k: True))
    fake = _FakeDevice()
    window.ctx.device = fake
    window.ctx.set_selection(0x01)
    page = window.pages["mode_params"]
    page._send()
    assert _wait_for(qapp, lambda: "一致" in page.result_label.text())
    assert page.mismatch.rowCount() == 0


def test_emergency_stop_sends_stop_all_and_park(window):
    """退出/断开前必须尽力发 STOP_ALL + TRIG_PARK（属于安全行为硬要求）。"""
    fake = _FakeDevice()
    window.ctx.device = fake
    window.emergency_stop(announce=True, synchronous=True)
    cmds = [c for c, _p, _ch in fake.t.requests]
    assert C.CMD_STOP_ALL in cmds
    assert C.CMD_TRIG_PARK in cmds


def test_play_checks_safety_before_playing(window, qapp, monkeypatch):
    """播放前必须调用 safety_check；不允许时弹确认框（这里模拟用户取消）。"""
    from awtest.ui import UiContext

    calls = []
    confirms = []

    def _deny(mask, override=False):
        calls.append((mask, override))
        return False, "选中通道中有 3 路满幅，超过限制 2"

    def _confirm(parent, title, text, detail="", danger=False):
        confirms.append(title)
        return False

    monkeypatch.setattr(UiContext, "confirm", staticmethod(_confirm))
    fake = _FakeDevice()
    fake.safety_check = _deny
    window.ctx.device = fake
    window.ctx.set_selection(0x07)
    window.pages["channels"]._play()
    assert _wait_for(qapp, lambda: bool(confirms)), "播放前没有做安全检查/确认"
    assert calls and calls[0][0] == 0x07
    assert "满幅" in confirms[0]
    # 用户取消 → 不应发出 PLAY / PLAY_MULTI
    cmds = [c for c, _p, _ch in fake.t.requests]
    assert C.CMD_PLAY not in cmds
    assert C.CMD_PLAY_MULTI not in cmds


# --------------------------------------------------------------------------
# 持续振动配方（首轮实测记录 §2.20d）
# --------------------------------------------------------------------------


def test_sustain_preset_sets_cont_recipe(window):
    """「一键持续振动参数」应把四个包络字段设成实测配方，并把模式切到 CONT。"""
    page = window.pages["mode_params"]
    # 先摆成数据手册默认值（就是那个会"一段一段"的组合）
    page.sp_drv1_time.setValue(4)
    page.sp_drv2_time.setValue(6)
    page.sp_brk_time.setValue(8)
    page.sp_wait_num.setValue(6)

    page.btn_sustain.click()

    assert page._current_mode() == C.MODE_CONT
    assert page.sp_drv1_time.value() == 255
    assert page.sp_drv2_time.value() == 0
    assert page.sp_brk_time.value() == 0
    assert page.sp_wait_num.value() == 0


def test_sustain_preset_clears_cont_warnings(window):
    """CONT 模式 + 手册默认值时应有黄色告警；套用配方后应清空。"""
    from PySide6.QtCore import Qt
    page = window.pages["mode_params"]
    # 先切到 CONT（告警只在 CONT 模式下才有意义）
    for row in range(page.mode_list.count()):
        item = page.mode_list.item(row)
        if item is not None and int(item.data(Qt.UserRole)) == C.MODE_CONT:
            page.mode_list.setCurrentRow(row)
            break
    page.sp_drv1_time.setValue(4)
    page.sp_drv2_time.setValue(6)
    page.sp_brk_time.setValue(8)
    page.sp_wait_num.setValue(6)
    page._on_mode_changed()
    assert "WAIT_NUM" in page.lbl_cont_warn.text(), \
        "手册默认值下应提示 WAIT_NUM 会导致断续"

    page.btn_sustain.click()
    assert page._current_mode() == C.MODE_CONT
    assert "WAIT_NUM" not in page.lbl_cont_warn.text()


def test_cont_warnings_flag_choppy_defaults(window):
    """Profile.warnings() 应指出 CONT 默认值不是持续振动配置。"""
    from awtest import profiles as P
    p = P.Profile()
    p.mode = C.MODE_CONT
    names = [n for n, _w in p.warnings()]
    assert "wait_num" in names
    assert "drv1_time" in names

    p.drv1_time = 255
    p.drv2_time = 0
    p.brk_time = 0
    p.wait_num = 0
    names = [n for n, _w in p.warnings()]
    assert "wait_num" not in names
    assert "drv1_time" not in names


def test_ram_loop_warning(window):
    """RAM 模式 mainloop != 0xF 时应提示"只播 N 轮"。"""
    from awtest import profiles as P
    p = P.Profile()
    p.mode = C.MODE_RAM
    p.mainloop = 0
    assert any(n == "mainloop" for n, _w in p.warnings())
    p.mainloop = 15
    assert not any(n == "mainloop" for n, _w in p.warnings())
