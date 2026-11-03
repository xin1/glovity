"""Wire contract and validation without opening a serial port."""
import struct
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from awtest.device import AWTestDevice
from awtest.live_lab import LiveParams, PRESETS, PRESET_DESCRIPTIONS
from awtest.protocol import commands as C


class EchoTransport:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def request(self, cmd, payload, **kwargs):
        self.calls.append((cmd, payload, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(payload=payload if self.reply is None else self.reply)


def test_default_fields_and_quantization():
    assert LiveParams().validate() is None
    assert LiveParams().encode_fields() == struct.pack("<HBBHBB", 1680, 30, 0, 20, 100, 1)
    params = LiveParams(frequency_hz=168.16, rate_hz=1.26, tracking=False)
    assert params.encode_fields() == struct.pack("<HBBHBB", 1682, 30, 0, 13, 100, 0)
    with pytest.raises(FrozenInstanceError):
        params.amplitude = 50


@pytest.mark.parametrize("field,value", [
    ("frequency_hz", v) for v in [59.99, 300.01, float("nan"), float("inf"), True, "168"]
] + [("rate_hz", v) for v in [0, 30.01, float("nan"), False]]
  + [("amplitude", v) for v in [-1, 78, 30.0, True]]
  + [("envelope", v) for v in [-1, 5, 1.0, False]]
  + [("depth", v) for v in [-1, 101, 50.0, True]]
  + [("tracking", v) for v in [0, 1, "true", None]])
def test_invalid_fields_rejected_before_transport(field, value):
    params = replace(LiveParams(), **{field: value})
    transport = EchoTransport()
    with pytest.raises(ValueError):
        AWTestDevice(transport).live_control(0x10, params, start=True)
    assert not transport.calls


@pytest.mark.parametrize("tracking,mode", [(True, C.MODE_CONT), (False, C.MODE_RTP)])
def test_start_ack_commits_only_selected_channels(tracking, mode):
    transport = EchoTransport()
    dev = AWTestDevice(transport)
    params = LiveParams(tracking=tracking)
    assert dev.live_control(0x50, params, start=True) is None
    assert transport.calls[0][0:2] == (0x4D, bytes((0, 0x50)) + params.encode_fields())
    assert len(transport.calls[0][1]) == 10
    assert {ch for ch, playing in dev.playing.items() if playing} == {4, 6}
    assert dev.modes[4] == dev.modes[6] == mode
    assert dev.modes[5] == C.MODE_RAM


def test_update_does_not_mark_idle_channels_playing():
    transport = EchoTransport()
    dev = AWTestDevice(transport)
    dev.live_control(0x10, LiveParams())
    assert transport.calls[0][1][0] == 1
    assert not any(dev.playing.values())
    assert dev.modes[4] == C.MODE_CONT


@pytest.mark.parametrize("reply", [b"", bytes((0, 0x10)), bytes(10), bytes(11)])
def test_bad_ack_does_not_commit_cache(reply):
    dev = AWTestDevice(EchoTransport(reply=reply))
    with pytest.raises(ValueError):
        dev.live_control(0x10, LiveParams(), start=True)
    assert not any(dev.playing.values())
    assert dev.modes[4] == C.MODE_RAM


def test_transport_error_does_not_commit_cache():
    dev = AWTestDevice(EchoTransport(error=TimeoutError("test")))
    with pytest.raises(TimeoutError):
        dev.live_control(0x10, LiveParams(), start=True)
    assert not any(dev.playing.values())


@pytest.mark.parametrize("mask", [0, -1, 256, 16.0, True, None])
def test_mask_rejected_before_transport(mask):
    transport = EchoTransport()
    dev = AWTestDevice(transport)
    for method in (lambda: dev.live_control(mask, LiveParams()), lambda: dev.live_keepalive(mask)):
        with pytest.raises(ValueError):
            method()
    assert not transport.calls


def test_keepalive_is_separate_command_with_exact_echo():
    transport = EchoTransport()
    assert AWTestDevice(transport).live_keepalive(0xF0) is None
    assert transport.calls[0][0:2] == (0x4E, b"\xf0")
    for reply in (b"", b"\x10", b"\xf0\x00"):
        with pytest.raises(ValueError):
            AWTestDevice(EchoTransport(reply=reply)).live_keepalive(0xF0)


def test_presets_and_command_registration():
    assert set(PRESETS) == set(PRESET_DESCRIPTIONS)
    assert len(PRESETS) == 6
    for name, params in PRESETS.items():
        assert len(params.encode_fields()) == 8
        assert params.tracking
        assert "候选" in PRESET_DESCRIPTIONS[name]
    assert C.CAP_LIVE_LAB == 1 << 18
    assert C.CMD_NAMES[C.CMD_LIVE_CONTROL] == "LIVE_CONTROL"
    assert C.CMD_NAMES[C.CMD_LIVE_KEEPALIVE] == "LIVE_KEEPALIVE"


@pytest.mark.parametrize("frequency", [60.0, 167.9, 168.01, 300.0])
def test_tracking_requires_fixed_resonance_seed(frequency):
    with pytest.raises(ValueError, match="168.0"):
        LiveParams(frequency_hz=frequency, tracking=True).encode_fields()
    assert len(LiveParams(frequency_hz=frequency, tracking=False).encode_fields()) == 8


def test_update_engine_cache_only_after_successful_ack():
    transport = EchoTransport()
    dev = AWTestDevice(transport)
    dev.live_control(0x10, LiveParams(), start=True)
    dev.live_control(0x10, LiveParams(tracking=False))
    assert dev.modes[4] == C.MODE_RTP
    assert dev.playing[4]
    transport.reply = b"bad ack"
    with pytest.raises(ValueError):
        dev.live_control(0x10, LiveParams(tracking=True))
    assert dev.modes[4] == C.MODE_RTP
    assert dev.playing[4]
