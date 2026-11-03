from test_quick_control import page, wait
from awtest.protocol import commands as C


def test_cont_embedded_default_and_timed_controls(page):
    w, ctx, app = page
    w.show()
    app.processEvents()
    assert w.live_control.isVisible()
    assert not w.parameters.isVisible()
    assert w.live_control.tracking.isChecked()
    assert not w.live_control.frequency.isEnabled()
    w.cont_kind.setCurrentIndex(1)
    assert w.parameters.isVisible()
    assert not w.live_control.isVisible()
    w.set_mode(C.MODE_RAM)
    assert w.preset_row.isVisible() and w.btn_start.isVisible()
    assert '播放' in w.btn_start.text()
    w.set_mode(C.MODE_TRIG)
    assert w.preset_row.isVisible() and '播放' in w.btn_start.text()
    assert 'BOOT' in w.btn_apply.text()
    assert not ctx.device.calls


def test_leaving_running_live_requests_stop(page):
    w, ctx, app = page
    stopped = []
    w.stop_requested.connect(lambda: stopped.append(True))
    w.live_control._mask = 0x10
    w.live_control._lease_timer.start()
    w.set_mode(C.MODE_RAM)
    assert stopped == [True]
    assert not w.live_control._mask
    assert not w.live_control._lease_timer.isActive()


def test_ram_preset_play_prepares_before_deploy(page, monkeypatch):
    w, ctx, app = page
    calls = []
    def prepare(dev, mask, name, token, progress, *, allow_replace):
        calls.append((mask, name, allow_replace))
        return 1, C.RATE_12K
    monkeypatch.setattr('awtest.ui.page_control.prepare_preset', prepare)
    w.set_mode(C.MODE_RAM)
    w.wave_preset.setCurrentIndex(1)
    w.allow_replace.setChecked(True)
    w.btn_start.click()
    assert wait(app, lambda: not w._busy)
    assert calls and calls[0][2]
    assert any(c[0] == 'play' for c in ctx.device.calls)
    p = ctx.device.profiles[4]
    assert p.wavseq == [1, 0, 0, 0, 0, 0, 0, 0]
    assert p.seq_loop == [0] * 4 and p.mainloop == 0


def test_trig_play_and_apply_are_distinct(page, monkeypatch):
    w, ctx, app = page
    monkeypatch.setattr('awtest.ui.page_control.prepare_preset', lambda *a, **k: (1, C.RATE_12K))
    w.set_mode(C.MODE_TRIG)
    w.wave_preset.setCurrentIndex(1)
    w.btn_apply.click()
    assert wait(app, lambda: not w._busy)
    assert not any(c[0] == 'trig' for c in ctx.device.calls)
    assert ctx.device.boot_mask == 0x10
    w.btn_start.click()
    assert wait(app, lambda: not w._busy)
    assert any(c[0] == 'trig' for c in ctx.device.calls)


def test_mainwindow_has_no_separate_live_tab():
    from PySide6.QtWidgets import QApplication
    from awtest.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    assert all(w.tabs.tabText(i) != '实时调试' for i in range(w.tabs.count()))
    assert w.page_live is w.page_control.live_control
    assert w.page_control.isAncestorOf(w.page_live)
    w.ctx.shutdown()
    w.close()


def test_invalid_trig_mask_never_prepares_wave(page, monkeypatch):
    w, ctx, app = page
    ctx.device.shared = True
    ctx.set_selection(0x20)
    calls = []
    monkeypatch.setattr('awtest.ui.page_control.prepare_preset', lambda *a, **k: calls.append(True))
    w.set_mode(C.MODE_TRIG)
    w.wave_preset.setCurrentIndex(1)
    w.btn_start.click()
    assert wait(app, lambda: not w._busy)
    assert not calls and not ctx.device.calls
    assert '共用' in w.result.text()


def test_preset_save_import_keeps_preset_identity(page, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    w, ctx, app = page
    path = str(tmp_path / 'preset.json')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (path, 'JSON'))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (path, 'JSON'))
    w.set_mode(C.MODE_RAM)
    w.wave_id.setValue(7)
    w.rate.setCurrentIndex(w.rate.findData(C.RATE_24K))
    w.wave_preset.setCurrentIndex(2)
    name = w.wave_preset.currentData()
    w.save_profile()
    w.wave_preset.setCurrentIndex(0)
    w.load_profile()
    assert w.wave_preset.currentData() == name
    assert not w.allow_replace.isChecked()


def test_prepared_wave_id_and_rate_become_custom_controls(page, monkeypatch):
    w, ctx, app = page
    monkeypatch.setattr('awtest.ui.page_control.prepare_preset', lambda *a, **k: (1, C.RATE_12K))
    w.set_mode(C.MODE_RAM)
    w.wave_id.setValue(7)
    w.rate.setCurrentIndex(w.rate.findData(C.RATE_24K))
    w.wave_preset.setCurrentIndex(1)
    w.btn_start.click()
    assert wait(app, lambda: not w._busy)
    w.wave_preset.setCurrentIndex(0)
    assert w.wave_id.value() == 1
    assert w.rate.currentData() == C.RATE_12K
