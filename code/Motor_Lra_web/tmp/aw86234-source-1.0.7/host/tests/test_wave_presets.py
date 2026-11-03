import base64
import json
import threading
from types import SimpleNamespace as NS

import pytest

from awtest import wave_presets as P
from awtest.protocol import codec as K, commands as C


class Device:
    def __init__(self, waves=None):
        self.waves = waves or {4: [], 5: []}
        self.rates = {ch: C.RATE_12K for ch in self.waves}
        self.bases = {ch: 2048 for ch in self.waves}
        self.events = []
        self.cancel = None
        self.short_read = False

    def get_profile(self, ch):
        return NS(base_addr=self.bases[ch], wavdat_mode=self.rates[ch])

    def wave_get_library(self, ch):
        entries = []
        offset = self.bases[ch] + 1 + 4 * len(self.waves[ch])
        for i, data in enumerate(self.waves[ch], 1):
            entries.append(K.WaveLibEntry(i, offset, offset + len(data) - 1))
            offset += len(data)
        return K.WaveLibrary(7, offset - self.bases[ch], self.bases[ch], entries)

    def wave_readback(self, ch, wid, off, count):
        data = self.waves[ch][wid - 1][off:off + count]
        if self.short_read:
            data = data[:-1]
        return K.WaveReadbackResp(wid, off, data)

    def boot_arm(self, mask):
        self.events.append(("boot", mask))

    def stop(self, ch):
        self.events.append(("stop", ch))

    def wave_begin(self, ch, rate, count):
        self.events.append(("begin", ch))
        self.pending = bytearray()
        return K.WaveBeginResp(1, 1019, 1024)

    def wave_add(self, ch, wid, off, total, chunk, last=False):
        self.events.append(("add", ch))
        self.pending.extend(chunk)
        if self.cancel:
            self.cancel.set()
        return K.WaveAddResp(wid, off)

    def wave_commit(self, ch, version):
        self.events.append(("commit", ch))
        self.waves[ch] = [bytes(self.pending)]
        self.rates[ch] = C.RATE_12K
        return K.WaveCommitResp()

    def wave_abort(self, ch):
        self.events.append(("abort", ch))


def test_candidates_compact_and_distinct():
    assert len(set(P.WAVE_PRESETS.values())) == 4
    for b in P.WAVE_PRESETS.values():
        assert len(b) + 5 <= 1024
        assert b[0] == b[-1] == 0
        assert max(abs(v if v < 128 else v - 256) for v in b) <= 90


def test_empty_upload_stopped_and_verified():
    d = Device()
    assert P.prepare_preset(d, 0x30, P.PRESET_NAMES[0]) == (1, C.RATE_12K)
    assert d.events[:3] == [("boot", 0), ("stop", 4), ("stop", 5)]
    assert d.waves[4] == d.waves[5] == [P.WAVE_PRESETS[P.PRESET_NAMES[0]]]


def test_reuse_preserves_all_ids_and_bytes():
    b = P.preset_samples(P.PRESET_NAMES[0], C.RATE_24K)
    d = Device({4: [b'original', b], 5: [b'other', b]})
    d.rates = {4: C.RATE_24K, 5: C.RATE_24K}
    assert P.prepare_preset(d, 0x30, P.PRESET_NAMES[0]) == (2, C.RATE_24K)
    assert not d.events
    assert d.waves[4][0] == b'original'


def test_default_refuses_existing_without_any_device_write():
    d = Device({4: [], 5: [b'original']})
    with pytest.raises(ValueError, match='已有不同'):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0])
    assert not d.events


def test_backups_capture_every_sample_and_metadata_before_write(tmp_path):
    d = Device({4: [bytes(range(200)), b'second'], 5: [b'other']})
    original_begin = d.wave_begin
    def begin(*args):
        assert len(list(tmp_path.glob('*.json'))) == 1
        return original_begin(*args)
    d.wave_begin = begin
    P.prepare_preset(d, 0x30, P.PRESET_NAMES[0], allow_replace=True, backup_dir=tmp_path)
    backup = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    first = backup['libraries'][0]
    assert first['rate_code'] == C.RATE_12K and first['lib_version'] == 7
    assert first['base_addr'] == 2048
    assert [w['wave_id'] for w in first['waves']] == [1, 2]
    assert base64.b64decode(first['waves'][0]['samples_base64']) == bytes(range(200))
    assert base64.b64decode(first['waves'][1]['samples_base64']) == b'second'


def test_backup_failure_no_mutation(tmp_path):
    d = Device({4: [b'original']})
    bad = tmp_path / 'file'
    bad.write_text('not a directory')
    with pytest.raises(OSError):
        P.prepare_preset(d, 0x10, P.PRESET_NAMES[0], allow_replace=True, backup_dir=bad)
    assert not d.events


def test_short_read_no_mutation(tmp_path):
    d = Device({4: [b'original']})
    d.short_read = True
    with pytest.raises(ValueError, match='读回不完整'):
        P.prepare_preset(d, 0x10, P.PRESET_NAMES[0], allow_replace=True, backup_dir=tmp_path)
    assert not d.events
    assert not list(tmp_path.iterdir())


def test_full_sram_preflight_all_targets():
    d = Device()
    d.bases[5] = 3000
    with pytest.raises(ValueError, match='空间不足'):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0])
    assert not d.events


def test_cancel_before_any_write():
    d = Device()
    with pytest.raises(P.PresetCancelled):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0], cancel=lambda: True)
    assert not d.events


def test_cancel_between_chunks_aborts_and_stops():
    d = Device()
    d.cancel = threading.Event()
    with pytest.raises(P.PresetCancelled):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0], cancel=d.cancel)
    assert sum(kind == 'add' for kind, ch in d.events) == 1
    assert ('abort', 4) in d.events
    assert not any(kind == 'commit' for kind, ch in d.events)
    assert d.events[-2:] == [('stop', 4), ('stop', 5)]


def test_later_channel_read_failure_does_not_mutate_first():
    d = Device({4: [], 5: [b'original']})
    d.short_read = True
    with pytest.raises(ValueError):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0], allow_replace=True)
    assert not d.events

def test_reuse_checks_cancel_after_progress():
    b = P.WAVE_PRESETS[P.PRESET_NAMES[0]]
    d = Device({4: [b]})
    cancel = threading.Event()
    with pytest.raises(P.PresetCancelled):
        P.prepare_preset(d, 0x10, P.PRESET_NAMES[0], cancel=cancel,
                         progress=lambda text: cancel.set())
    assert not d.events


def test_verification_failure_stops_all_targets():
    d = Device()
    commit = d.wave_commit
    def corrupt_commit(ch, version):
        result = commit(ch, version)
        d.waves[ch] = [bytes(len(d.waves[ch][0]))]
        return result
    d.wave_commit = corrupt_commit
    with pytest.raises(ValueError, match='读回校验失败'):
        P.prepare_preset(d, 0x30, P.PRESET_NAMES[0])
    assert d.events[-2:] == [('stop', 4), ('stop', 5)]
    assert ('begin', 5) not in d.events
