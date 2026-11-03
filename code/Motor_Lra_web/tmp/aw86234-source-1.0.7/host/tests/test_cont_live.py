from types import SimpleNamespace
from threading import Event
from test_quick_control import page, wait
from awtest.quick_control import continuous_profile


def prepare(widget, ctx):
    calls = []
    levels = {}
    ctx.device.get_profile = lambda ch: continuous_profile()
    ctx.device.get_play_state = lambda ch: SimpleNamespace(playing=True)
    ctx.device.reg_read_u8 = lambda ch, addr: 0x85 if addr == 0x1D else levels[ch] | 0x80
    def live(ch, d1, d2, noresp=True):
        calls.append((ch, d1, d2, noresp))
        levels[ch] = d1
    ctx.device.set_cont_live = live
    widget._live_mask = 0x10
    return calls


def test_live_coalesces_drag_and_preserves_other_drive(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    for level in (41, 42, 43, 44, 45):
        w.level.setValue(level)
    assert wait(app, lambda: len(calls) == 1 and not w._live_inflight)
    assert calls == [(4, 45, 5, False)]
    assert not ctx.device.calls  # No STOP, GO or timed-play commands.


def test_live_stop_cancels_pending(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    w.level.setValue(50)
    w.cancel_pending()
    w._flush_live()
    assert not calls


def test_live_skips_stopped_motor(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    ctx.device.get_play_state = lambda ch: SimpleNamespace(playing=False)
    w.level.setValue(50)
    w._flush_live()
    assert wait(app, lambda: not w._live_inflight)
    assert not calls


def test_live_skips_non_cont_motor(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    ctx.device.get_profile = lambda ch: SimpleNamespace(mode=0)
    w.level.setValue(50)
    w._flush_live()
    assert wait(app, lambda: not w._live_inflight)
    assert not calls


def test_live_latest_value_follows_inflight_and_cancel_hides_old_result(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    entered, release = Event(), Event()
    original = ctx.device.set_cont_live
    def live(*args, **kwargs):
        entered.set()
        release.wait(2)
        original(*args, **kwargs)
    ctx.device.set_cont_live = live
    w.level.setValue(41)
    w._flush_live()
    assert entered.wait(1)
    for level in (42, 43, 44):
        w.level.setValue(level)
    release.set()
    assert wait(app, lambda: len(calls) == 2 and not w._live_inflight)
    assert [c[1] for c in calls] == [41, 44]
    w.level.setValue(45)
    w.cancel_pending()
    old = w.result.text()
    w._flush_live()
    app.processEvents()
    assert w.result.text() == old
    assert len(calls) == 2


def test_live_selection_and_import_loading_cancel(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    w.level.setValue(50)
    ctx.set_selection(0x20)
    w._flush_live()
    assert not calls
    w._live_mask = 0x20
    w._load_controls()
    assert not w._live_mask


def test_live_hide_cancels_queued_write(page):
    w, ctx, app = page
    w.show()
    calls = prepare(w, ctx)
    w.level.setValue(50)
    w.hide()
    w._flush_live()
    assert not calls


def test_live_high_level_invalidates_old_callback(page):
    w, ctx, app = page
    calls = prepare(w, ctx)
    entered, release = Event(), Event()
    original = ctx.device.set_cont_live
    def live(*args, **kwargs):
        entered.set()
        release.wait(2)
        original(*args, **kwargs)
    ctx.device.set_cont_live = live
    w.level.setMaximum(127)
    w.level_slider.setMaximum(127)
    w.level.setValue(60)
    w._flush_live()
    assert entered.wait(1)
    w.level.setValue(90)
    release.set()
    assert wait(app, lambda: not w._live_inflight)
    assert '更高电平须重新下发' in w.result.text()
    assert calls == [(4, 60, 5, False)]
