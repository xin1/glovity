import os
import time
from threading import Event
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from awtest.protocol import commands as C, codec as K
from awtest.ui import UiContext
from awtest.ui.page_live import PageLive


def wait(app, predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return predicate()


class Device:
    is_open = True
    def __init__(self):
        self.calls = []
        self.caps = C.CAP_LIVE_LAB
        self.present = 0xF0
        self.failure = False
    def get_caps(self): return K.Caps(caps=self.caps)
    def ch_probe_all(self): return SimpleNamespace(present_mask=self.present)
    def live_control(self, mask, params, start=False):
        params.validate()
        if self.failure: raise ValueError('test error')
        self.calls.append(('start' if start else 'update', mask, params))
    def live_keepalive(self, mask): self.calls.append(('renew', mask))
    def stop_all(self): self.calls.append(('stop',))


@pytest.fixture
def live():
    app = QApplication.instance() or QApplication([])
    ctx = UiContext()
    ctx.device = Device()
    ctx.set_selection(0x10)
    page = PageLive(ctx)
    yield page, ctx, app
    page.cancel_pending()
    ctx.shutdown()
    page.close()


def started(live):
    p, ctx, app = live
    p.start()
    assert wait(app, lambda: not p._starting)
    assert p._mask == 0x10
    return p, ctx, app


def test_tracking_locks_frequency_and_presets_preserve_choice(live):
    p, ctx, app = live
    assert not p.frequency.isEnabled()
    p.tracking.setChecked(False)
    assert p.frequency.isEnabled()
    p.frequency.setValue(210)
    p.presets.setCurrentText('轻柔呼吸')
    assert not p.tracking.isChecked() and p.params().frequency_hz == 210
    p.tracking.setChecked(True)
    assert p.params().frequency_hz == 168
    assert not p.frequency.isEnabled()
    p.tracking.setChecked(False)
    assert p.params().frequency_hz == 210
    assert not ctx.device.calls


def test_running_changes_coalesce_and_presets_apply(live):
    p, ctx, app = started(live)
    p._lease_timer.stop()
    for n in (31, 32, 33, 34): p.amplitude.setValue(n)
    assert wait(app, lambda: any(c[0] == 'update' for c in ctx.device.calls) and not p._updating)
    assert len(ctx.device.calls) == 2
    assert ctx.device.calls[-1][2].amplitude == 34
    p.presets.setCurrentText('双击节奏')
    assert wait(app, lambda: len(ctx.device.calls) == 3 and not p._updating)
    assert ctx.device.calls[-1][2].envelope == 3
    assert sum(c[0] == 'start' for c in ctx.device.calls) == 1


def test_lease_and_stop_cancellation(live):
    p, ctx, app = started(live)
    assert wait(app, lambda: any(c[0] == 'renew' for c in ctx.device.calls))
    p.amplitude.setValue(40)
    ctx.cancel_operations.emit()
    n = len(ctx.device.calls)
    p._flush(); p._renew()
    app.processEvents()
    assert len(ctx.device.calls) == n
    assert not p._lease_timer.isActive() and not p._mask


def test_changing_selection_keeps_running_target(live):
    p, ctx, app = started(live)
    p._lease_timer.stop()
    ctx.set_selection(0x20)
    p.amplitude.setValue(40)
    assert wait(app, lambda: len(ctx.device.calls) == 2 and not p._updating)
    assert ctx.device.calls[-1][1] == 0x10
    assert '下次启动' in p.targets.text()


def test_old_firmware_and_offline_block_before_start(live):
    p, ctx, app = live
    ctx.device.caps = 0
    p.start()
    assert wait(app, lambda: not p._starting)
    assert not ctx.device.calls and not p._mask
    assert '1.0.3' in p.status.text()
    ctx.device.caps = C.CAP_LIVE_LAB
    ctx.device.present = 0
    p.start()
    assert wait(app, lambda: not p._starting)
    assert not ctx.device.calls


def test_cancel_inflight_start_stops_without_renewal(live):
    p, ctx, app = live
    entered, release = Event(), Event()
    original = ctx.device.live_control
    def control(*args, **kwargs):
        entered.set(); release.wait(2); original(*args, **kwargs)
    ctx.device.live_control = control
    p.start()
    assert entered.wait(1)
    p.cancel_pending()
    release.set()
    assert wait(app, lambda: not p._starting)
    assert [c[0] for c in ctx.device.calls] == ['start', 'stop']
    assert not p._mask and not p._lease_timer.isActive()


def test_update_failure_stops_and_disarms(live):
    p, ctx, app = started(live)
    ctx.device.failure = True
    p.amplitude.setValue(40)
    assert wait(app, lambda: not p._mask)
    assert ctx.device.calls[-1] == ('stop',)
    assert not p._lease_timer.isActive()


def test_disconnection_never_restarts(live):
    p, ctx, app = started(live)
    p.on_connection_changed(False)
    p.on_connection_changed(True)
    n = len(ctx.device.calls)
    p._renew(); p._flush()
    assert len(ctx.device.calls) == n and not p._mask


def test_mode_switch_is_update_and_freq_locked(live):
    p, ctx, app = started(live)
    p._lease_timer.stop()
    p.tracking.setChecked(False)
    p.frequency.setValue(190)
    assert wait(app, lambda: len(ctx.device.calls) == 2 and not p._updating)
    assert not ctx.device.calls[-1][2].tracking
    p.tracking.setChecked(True)
    assert wait(app, lambda: len(ctx.device.calls) == 3 and not p._updating)
    assert ctx.device.calls[-1][2].tracking and ctx.device.calls[-1][2].frequency_hz == 168
    assert not p.frequency.isEnabled()


def test_live_no_programmed_end_and_can_keep_running_hidden(live):
    p, ctx, app = started(live)
    p.show(); p.hide()
    assert p._lease_timer.isActive() and p._mask
    assert p.start_button.text() == '持续运行中'
    p.start()
    assert sum(c[0] == 'start' for c in ctx.device.calls) == 1


def test_terminal_done_during_update_invalidates_late_ack(live):
    p, ctx, app = started(live)
    entered, release = Event(), Event()
    original = ctx.device.live_control
    def update(*args, **kwargs):
        entered.set(); release.wait(2); original(*args, **kwargs)
    ctx.device.live_control = update
    p.amplitude.setValue(40)
    p._flush()
    assert entered.wait(1)
    ctx.bridge.event_received.emit(SimpleNamespace(cmd=C.EVT_PLAY_DONE, chan=4))
    release.set()
    assert wait(app, lambda: not p._updating)
    assert not p._mask and not p._lease_timer.isActive()
    assert '播放已结束' in p.status.text()


def test_renew_failure_requests_stop_and_does_not_restart(live):
    p, ctx, app = started(live)
    def failure(mask): raise ValueError('lost lease')
    ctx.device.live_keepalive = failure
    p._renew()
    assert wait(app, lambda: not p._mask)
    assert ctx.device.calls[-1] == ('stop',)
    assert not p._lease_timer.isActive()
