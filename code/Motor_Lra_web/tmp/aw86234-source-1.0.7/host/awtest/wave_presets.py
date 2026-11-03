"""Small RAM/TRIG candidates with explicit, backed-up replacement semantics.

Current firmware cannot concatenate multiple uploaded waves safely. Consequently
we only upload a single preset, or reuse an exact existing waveform. No append
or automatic deletion of a user's library is attempted.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType

from .protocol import commands as C
from .protocol.codec import WaveLibEntry

PRESET_NAMES = ("短促轻点", "柔和起落", "双击", "渐弱敲击")
SRAM_BYTES = 3072


class PresetCancelled(RuntimeError):
    pass


def _check(cancel):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise PresetCancelled("预设准备已取消；未启动播放")


def preset_samples(name, rate_code=C.RATE_12K):
    """Signed int8, 168 Hz, conservative peak 90/127; 18..36 ms."""
    if name not in PRESET_NAMES:
        raise ValueError(f"未知波形预设：{name}")
    if rate_code not in (C.RATE_12K, C.RATE_12K_ALT, C.RATE_24K):
        raise ValueError("预设仅支持 12/24 kHz")
    duration = (0.018, 0.030, 0.036, 0.024)[PRESET_NAMES.index(name)]
    rate = C.RATE_HZ[rate_code]
    n = round(duration * rate)
    out = []
    for i in range(n):
        x = i / (n - 1)
        if name == "双击":
            phase = x / 0.35 if x < 0.35 else (x - 0.65) / 0.35 if x > 0.65 else 0
            envelope = math.sin(math.pi * phase) ** 2
        elif name == "渐弱敲击":
            envelope = min(1, x / 0.08) * (1 - x) ** 2
        else:
            envelope = math.sin(math.pi * x) ** (2 if name == "柔和起落" else 1)
        out.append(round(90 * envelope * math.sin(2 * math.pi * 168 * i / rate)) & 255)
    return bytes(out)


WAVE_PRESETS = MappingProxyType({name: preset_samples(name) for name in PRESET_NAMES})


def _read_wave(device, ch, entry, cancel):
    data = bytearray()
    while len(data) < entry.length:
        _check(cancel)
        offset = len(data)
        size = min(C.REG_MAX_LEN, entry.length - offset)
        rb = device.wave_readback(ch, entry.wave_id, offset, size)
        if rb.wave_id != entry.wave_id or rb.offset != offset or len(rb.samples) != size:
            raise ValueError(f"CH{ch} 波形 {entry.wave_id} 读回不完整，未改写设备")
        data.extend(rb.samples)
    return bytes(data)


def _snapshot(device, ch, cancel):
    _check(cancel)
    profile = device.get_profile(ch)
    library = device.wave_get_library(ch)
    base = library.base_addr
    if base != profile.base_addr or not 0 <= base < SRAM_BYTES:
        raise ValueError(f"CH{ch} 波形库基址不一致，未改写设备")
    entries = library.entries
    if len(entries) > C.WAVE_MAX_WAVES:
        raise ValueError(f"CH{ch} 波形库条目数无效")
    cursor = base + 1 + 4 * len(entries)
    waves = []
    for expected, entry in enumerate(entries, 1):
        if (entry.wave_id != expected or entry.start != cursor or entry.length <= 0
                or entry.end >= SRAM_BYTES):
            raise ValueError(f"CH{ch} 波形库地址表无效；请先在波形页检查，未改写设备")
        waves.append((entry.wave_id, _read_wave(device, ch, entry, cancel)))
        cursor = entry.end + 1
    if entries and library.total_bytes != cursor - base:
        raise ValueError(f"CH{ch} 波形库长度不一致，未改写设备")
    return {"channel": ch, "base_addr": base, "lib_version": library.lib_version,
            "rate_code": profile.wavdat_mode, "waves": waves}


def _backup(snapshots, name, backup_dir):
    root = Path(backup_dir) if backup_dir is not None else Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "AW86234" / "waveform-backups"
    root.mkdir(parents=True, exist_ok=True)
    document = {"format": "aw86234-wave-backup-v1", "replacement_preset": name,
                "libraries": [{**s, "waves": [{"wave_id": i, "samples_base64": base64.b64encode(b).decode("ascii")}
                                               for i, b in s["waves"]]} for s in snapshots]}
    # A unique file, flushed to disk before the first device write. Never overwrite backups.
    fd, filename = tempfile.mkstemp(prefix="before-preset-", suffix=".json", dir=root)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return Path(filename)


def prepare_preset(device, mask, name, cancel=None, progress=None, *,
                   allow_replace=False, backup_dir=None):
    """Return (shared wave ID, rate code), without starting playback.

    Preflight and read every targeted library before writing. Existing samples
    are reused when possible. Differing libraries require explicit replacement
    consent and a successfully flushed local backup first. There is no multi-
    channel atomic commit: failure may leave earlier targets updated, but every
    replacement has a backup and all targets are stopped on failure.
    """
    if type(mask) is not int or not 0 < mask <= 255:
        raise ValueError("请选择有效目标通道")
    preset_samples(name)  # validate before IO
    channels = [ch for ch in range(8) if mask & (1 << ch)]
    snapshots = [_snapshot(device, ch, cancel) for ch in channels]
    _check(cancel)
    rates = {s["rate_code"] for s in snapshots}
    if len(rates) == 1 and next(iter(rates)) in (C.RATE_12K, C.RATE_12K_ALT, C.RATE_24K):
        rate = next(iter(rates))
        wanted = preset_samples(name, rate)
        matches = [{i for i, data in s["waves"] if data == wanted} for s in snapshots]
        common = set.intersection(*matches)
        if common:
            if progress:
                progress("复用设备中已有预设波形")
            _check(cancel)
            return min(common), rate
    wanted = WAVE_PRESETS[name]
    changes = [s for s in snapshots if not (s["rate_code"] == C.RATE_12K
               and any(i == 1 and data == wanted for i, data in s["waves"]))]
    replacements = [s for s in changes if s["waves"]]
    if replacements and not allow_replace:
        raise ValueError("目标已有不同波形库；当前固件不支持安全追加。请勾选允许替换（原波形自动备份到本机），或使用已有波形")
    for s in changes:
        if 5 + len(wanted) > SRAM_BYTES - s["base_addr"]:
            raise ValueError(f"CH{s['channel']} SRAM 空间不足，未改写设备")
    if replacements:
        _check(cancel)
        path = _backup(replacements, name, backup_dir)
        if progress:
            progress(f"原波形已备份：{path}")
    _check(cancel)
    active = None
    try:
        device.boot_arm(0)
        for ch in channels:
            device.stop(ch)
        for s in changes:
            _check(cancel)
            ch = s["channel"]
            active = ch
            begin = device.wave_begin(ch, C.RATE_12K, 1)
            if begin.max_total_samples < len(wanted):
                raise ValueError(f"CH{ch} 固件报告 SRAM 空间不足")
            for off in range(0, len(wanted), C.WAVE_MAX_CHUNK_SAMPLES):
                _check(cancel)
                chunk = wanted[off:off + C.WAVE_MAX_CHUNK_SAMPLES]
                reply = device.wave_add(ch, 1, off, len(wanted), chunk,
                                        last=off + len(chunk) == len(wanted))
                if reply.wave_id != 1 or reply.offset_ack != off:
                    raise ValueError(f"CH{ch} 波形分片应答不一致")
            _check(cancel)
            result = device.wave_commit(ch, 1)
            if result.result != 0:
                raise ValueError(f"CH{ch} 波形提交失败：{result.result}")
            active = None
            # Firmware CRC checks its staging buffer, so independently check desired bytes.
            entry = WaveLibEntry(1, 0, len(wanted) - 1)
            if _read_wave(device, ch, entry, cancel) != wanted:
                raise ValueError(f"CH{ch} 预设读回校验失败；已停止播放")
            if progress:
                progress(f"CH{ch} 预设已准备")
        _check(cancel)
        return 1, C.RATE_12K
    except Exception:
        if active is not None:
            try:
                device.wave_abort(active)
            except Exception:
                pass
        for ch in channels:
            try:
                device.stop(ch)
            except Exception:
                pass
        raise



