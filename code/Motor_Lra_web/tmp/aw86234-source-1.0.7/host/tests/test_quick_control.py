import os
import time
from threading import Event
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

from awtest import profiles as P
from awtest.protocol import commands as C, codec as K
from awtest.quick_control import continuous_profile, deploy, DeploymentCancelled


class Device:
    is_open = True

    def __init__(self):
        self.calls = []
        self.present = 0xF0
        self.shared = False
        self.boot_mask = 0
        self.modern = True
        self.mismatches = []
        self.failure = None
        self.cancel_at_verify = None
        self.cancel_at_play = None
        self.started = None
        self.safe = True
        self.waves = [SimpleNamespace(wave_id=1)]
        self.profiles = {}
        self.playing = {}
        self.modes = {}

    def get_caps(self):
        return K.Caps(caps=(C.CAP_SHARED_TRIG_567 if self.shared else 0) | ((C.CAP_TIMED_PLAY | C.CAP_BOOT_TRIGGER) if self.modern else 0))

    def ch_probe_all(self):
        return SimpleNamespace(present_mask=self.present)

    def wave_get_library(self, ch):
        return SimpleNamespace(entries=self.waves)

    def stop(self, ch, immediate=True):
        self.calls.append(('stop', ch))
        self.playing[ch] = False

    def set_profile_verify(self, ch, profile):
        self.calls.append(('verify', ch))
        if self.failure:
            raise self.failure
        self.profiles[ch] = profile.copy()
        self.modes[ch] = profile.mode
        if self.cancel_at_verify:
            self.cancel_at_verify.set()
        return self.mismatches

    def safety_check(self, mask, override=False):
        self.calls.append(('safety', mask, override))
        return self.safe, '供电限制'

    def play_multi(self, mask, timeout_ms, skew_us, override):
        self.calls.append(('play', mask, timeout_ms, override))
        if self.cancel_at_play:
            self.cancel_at_play.set()
        return K.PlayMultiResp(mask if self.started is None else self.started, 0)

    def play_timed(self, mask, duration_ms):
        return self.play_multi(mask, duration_ms, 0, False)

    def boot_arm(self, mask):
        self.boot_mask = mask

    def trig_pulse(self, mask, **kwargs):
        self.calls.append(('trig', mask))
        return mask

    def get_play_state(self, ch):
        self.playing[ch] = True
        return SimpleNamespace(playing=True)

    def ch_get_info(self, ch):
        return SimpleNamespace(mode=self.modes.get(ch, C.MODE_RAM), playing=self.playing.get(ch, False))

    def reg_update(self, ch, addr, mask, value):
        self.calls.append(('reg_update', ch, addr, mask, value))

    def reg_write(self, ch, addr, data):
        self.calls.append(('reg_write', ch, addr, bytes(data)))


def run(dev, p=None, mask=0x30, start=True, token=None):
    return deploy(dev, p or continuous_profile(), mask, 2000, start, token or Event())


def test_cont_recipe_and_exact_selected_channels():
    dev = Device()
    result = run(dev, mask=0x20)
    assert result.started_mask == 0x20
    assert dev.calls == [('stop', 5), ('verify', 5), ('safety', 0x20, False), ('play', 0x20, 2000, False)]
    p = dev.profiles[5]
    assert (p.drv1_lvl, p.drv1_time, p.drv2_time, p.brk_time, p.wait_num, p.en_f0_det) == (40, 255, 0, 0, 0, 0)
    assert p.f_pre == 143
    assert (p.sin_mode, p.edge_fre) == (1, 14)


def test_all_channels_verified_before_start():
    dev = Device()
    run(dev)
    assert dev.calls[:4] == [('stop', 4), ('stop', 5), ('verify', 4), ('verify', 5)]
    assert dev.calls[-1][0] == 'play'


@pytest.mark.parametrize('mismatch', ['drv1_lvl', 'wait_num', 'mode', 'wavdat_mode'])
def test_applied_mismatch_blocks_start_and_stops_targets(mismatch):
    dev = Device()
    dev.mismatches = [(mismatch, 1, 2)]
    with pytest.raises(ValueError, match='校验不一致'):
        run(dev)
    assert not any(c[0] == 'play' for c in dev.calls)
    assert dev.calls[-2:] == [('stop', 4), ('stop', 5)]


def test_inactive_hardware_segment_does_not_falsely_block_cont():
    dev = Device()
    dev.mismatches = [('mainloop', 0, 15), ('trig1_stop', 1, 0), ('fifo_ae', 512, 10)]
    assert run(dev).started_mask == 0x30


@pytest.mark.parametrize('stage', ['before', 'verify', 'play'])
def test_cancellation_never_leaves_an_inflight_start_running(stage):
    dev, token = Device(), Event()
    if stage == 'before':
        token.set()
    elif stage == 'verify':
        dev.cancel_at_verify = token
    else:
        dev.cancel_at_play = token
    with pytest.raises(DeploymentCancelled):
        run(dev, token=token)
    if stage == 'before':
        assert not dev.calls
    else:
        assert dev.calls[-2:] == [('stop', 4), ('stop', 5)]
    if stage != 'play':
        assert not any(c[0] == 'play' for c in dev.calls)


def test_only_deploy_does_not_play():
    dev = Device()
    assert run(dev, start=False).started_mask == 0
    assert not any(c[0] in ('play', 'trig') for c in dev.calls)


@pytest.mark.parametrize('case', ['offline', 'timeout', 'mismatch', 'safety', 'partial'])
def test_failures_do_not_report_success(case):
    dev = Device()
    if case == 'offline': dev.present = 0
    if case == 'timeout': dev.failure = TimeoutError('设备无应答')
    if case == 'mismatch': dev.mismatches = [('f_pre', 143, 140)]
    if case == 'safety': dev.safe = False
    if case == 'partial': dev.started = 0x10
    with pytest.raises((ValueError, TimeoutError, RuntimeError)):
        run(dev)
    if case == 'offline':
        assert not dev.calls
    else:
        assert dev.calls[-2:] == [('stop', 4), ('stop', 5)]


def test_ram_missing_wave_is_actionable_and_does_not_write():
    dev = Device()
    dev.waves = []
    with pytest.raises(ValueError, match='波形编辑'):
        run(dev, P.preset_ram_175hz())
    assert not dev.calls


def test_trig_respects_actual_hardware_capability():
    dev = Device()
    assert run(dev, P.preset_trig_rising(), mask=0x20).started_mask == 0x20
    dev = Device()
    dev.shared = True
    with pytest.raises(ValueError, match='CH5/6/7'):
        run(dev, P.preset_trig_rising(), mask=0x20)
    assert not dev.calls
    assert run(dev, P.preset_trig_rising(), mask=0xE0).started_mask == 0xE0


def test_rtp_config_only():
    dev = Device()
    with pytest.raises(ValueError, match='数据流'):
        run(dev, P.preset_rtp_12k())
    assert run(dev, P.preset_rtp_12k(), start=False).started_mask == 0


def test_switch_from_cont_restores_braking_prerequisites_and_verifies_them():
    dev = Device()
    run(dev, P.preset_ram_175hz())
    assert ('reg_update', 4, 0x17, 0x20, 0) in dev.calls
    assert ('reg_write', 4, 0x20, b'\x08') in dev.calls
    dev = Device()
    dev.mismatches = [('brk_time', 8, 0)]
    with pytest.raises(ValueError, match='brk_time'):
        run(dev, P.preset_ram_175hz())
    assert not any(c[0] == 'play' for c in dev.calls)


@pytest.fixture
def page():
    from PySide6.QtWidgets import QApplication
    from awtest.ui import UiContext
    from awtest.ui.page_control import PageControl
    app = QApplication.instance() or QApplication([])
    ctx = UiContext()
    ctx.device = Device()
    ctx.set_selection(0x10)
    widget = PageControl(ctx)
    yield widget, ctx, app
    widget.cancel_pending()
    ctx.shutdown()
    widget.close()


def wait(app, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return predicate()


def test_mode_switch_keeps_drafts_without_hardware_commands(page):
    widget, ctx, app = page
    widget.level.setValue(53)
    widget.set_mode(C.MODE_RAM)
    widget.level.setValue(80)
    widget.set_mode(C.MODE_CONT)
    assert widget.level.value() == 53
    assert not ctx.device.calls
    assert widget.btn_start.isEnabled()


def test_quick_frequency_range_cannot_exceed_hidden_drive_width(page):
    widget, ctx, app = page
    widget.frequency.setValue(widget.frequency.maximum())
    assert not widget.profile().validate()


def test_one_click_deploy_and_duplicate_click_guard(page):
    widget, ctx, app = page
    widget.submit(True)
    widget.submit(True)
    assert wait(app, lambda: not widget._busy)
    assert len([c for c in ctx.device.calls if c[0] == 'play']) == 1
    assert '已启动' in widget.result.text()


def test_disconnected_and_rtp_controls_are_disabled(page):
    widget, ctx, app = page
    widget.set_mode(C.MODE_RTP)
    assert not widget.btn_start.isEnabled()
    assert widget.btn_apply.isEnabled()
    ctx.device.is_open = False
    widget.on_connection_changed(False)
    assert not widget.btn_apply.isEnabled()
    assert '断开' in widget.result.text()


def test_json_save_load_preserves_quick_parameters(page, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    widget, ctx, app = page
    target = str(tmp_path / 'preset.json')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (target, ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (target, ''))
    widget.level.setValue(63)
    widget.duration.setValue(1.234)
    widget.save_profile()
    widget.reset_cont()
    widget.load_profile()
    assert widget.level.value() == 63
    assert widget.duration.value() == 1.234
    assert not ctx.device.calls


def test_auto_brake_is_independent_of_trig_brake():
    from PySide6.QtWidgets import QApplication
    from awtest.ui import UiContext
    from awtest.ui.page_mode_params import PageModeParams
    app = QApplication.instance() or QApplication([])
    ctx = UiContext()
    widget = PageModeParams(ctx)
    try:
        assert widget.sp_gain.value() == 128
        widget.chk_auto_brk.setChecked(False)
        p = widget._sync_model()
        assert p.brk_en == 0 and p.trig1_brk == 1
    finally:
        widget.close()
        ctx.shutdown()


def test_import_preserves_alternate_rate_and_rejects_unrepresentable_trig(page, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    widget, ctx, app = page
    target = str(tmp_path / 'preset.json')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (target, ''))
    p = P.preset_ram_175hz()
    p.wavdat_mode = C.RATE_12K_ALT
    p.save(target)
    widget.load_profile()
    assert widget.profile().wavdat_mode == C.RATE_12K_ALT
    p = P.preset_trig_rising()
    p.trig1_seq_p, p.trig1_pos_en = 0, 0
    p.save(target)
    widget.load_profile()
    assert '导入失败' in widget.result.text()
    assert widget.profile().mode == C.MODE_RAM


def test_every_stop_entry_cancels_queued_deployment(page):
    from awtest.ui.page_channels import PageChannels
    widget, ctx, app = page
    channels = PageChannels(ctx)
    entered, release = Event(), Event()
    def blocker(progress):
        entered.set()
        assert release.wait(2)
    ctx.device.stop_all = lambda **kwargs: ctx.device.calls.append(('stop_all',)) or 0xFF
    ctx.device.trig_park = lambda: ctx.device.calls.append(('park',))
    ctx.task(blocker)
    try:
        assert entered.wait(1)
        widget.submit(True)
        channels.stop_all_and_park()
        release.set()
        assert wait(app, lambda: ctx.runner.busy == 0)
        assert ('stop_all',) in ctx.device.calls and ('park',) in ctx.device.calls
        assert not any(c[0] == 'play' for c in ctx.device.calls)
    finally:
        release.set()
        channels.close()


def test_compact_matrix_shows_online_state_and_selects_one_online_channel():
    from PySide6.QtWidgets import QApplication
    from awtest.ui.widgets.channel_matrix import ChannelMatrix
    app = QApplication.instance() or QApplication([])
    matrix = ChannelMatrix()
    matrix.set_compact(True)
    matrix.apply_probe_all(0xF0, {ch: 0x2340 for ch in range(4, 8)})
    assert matrix.selected_mask() == 0x10
    assert '在线' in matrix.card(4).state_label.text()
    assert '离线' in matrix.card(0).state_label.text()
    assert not matrix.card(4).state_label.isHidden()
    matrix.close()


def test_old_firmware_cannot_silently_use_safety_timeout_as_duration():
    dev = Device()
    dev.modern = False
    with pytest.raises(ValueError, match="更新"):
        run(dev)
    assert not dev.calls


def test_trig_apply_only_arms_boot_without_firing():
    dev = Device()
    run(dev, P.preset_trig_rising(), mask=0x20, start=False)
    assert dev.boot_mask == 0x20
    assert not any(c[0] in ('trig', 'play') for c in dev.calls)


def test_boot_arming_requires_uploaded_wave_even_without_start():
    dev = Device()
    dev.waves = []
    with pytest.raises(ValueError, match="缺少波形"):
        run(dev, P.preset_trig_rising(), start=False)
    assert dev.boot_mask == 0


def test_timed_wire_format_and_boot_ack():
    from awtest.device import AWTestDevice
    class Transport:
        def request(self, cmd, payload, **kwargs):
            self.last = cmd, payload
            return SimpleNamespace(payload=payload[:1])
    t = Transport()
    dev = AWTestDevice(t)
    assert dev.play_timed(0x20, 1234).started_mask == 0x20
    assert t.last == (C.CMD_PLAY_TIMED, b'\x20\xd2\x04')
    dev.boot_arm(0x30)
    assert t.last == (C.CMD_BOOT_ARM, b'\x30')


def test_normal_duration_event_is_not_a_safety_alert():
    assert '播放完成' in C.PLAY_DONE_REASON_NAMES[C.PLAY_DONE_DURATION]
    assert C.PLAY_DONE_DURATION != C.PLAY_DONE_SAFETY_TIMEOUT


@pytest.mark.parametrize('factory', [P.preset_ram_175hz, P.preset_trig_rising])
def test_ram_trig_restore_and_verify_wait_num(factory):
    dev = Device()
    run(dev, factory(), mask=0x10)
    assert ('reg_write', 4, 0x1A, b'\x06') in dev.calls
    dev.mismatches = [('wait_num', 6, 0)]
    with pytest.raises(ValueError, match='wait_num'):
        run(dev, factory(), mask=0x10)


def test_ram_trig_reject_zero_wait_before_hardware_writes():
    dev = Device()
    p = P.preset_trig_rising(); p.wait_num = 0
    with pytest.raises(ValueError, match='WAIT_NUM'):
        run(dev, p)
    assert not dev.calls
