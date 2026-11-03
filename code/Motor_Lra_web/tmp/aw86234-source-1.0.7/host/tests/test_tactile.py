import math
import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest
from awtest.tactile import Mapper, MappingConfig, Route, Sample
from awtest.tactile.paxini import read_request, decode_response, vector
from awtest.tactile.session import Session
from awtest.device import AWTestDevice
from awtest.protocol import commands as C
from awtest.protocol.transport import SerialTransport


def feed(mapper, n, fz, fx=0, sensor="finger", taxels=()):
    mapper.ingest(Sample(sensor, n, 1+n*.02, (fx,0,fz), taxels))
    return mapper.render(1+n*.02)


def test_silent_noise_and_release():
    m=Mapper()
    for n in range(100):
        assert feed(m,n,.1)[0] == (0,)*8
    assert feed(m,100,5)[0][4] > 0
    for n in range(101,130):
        result=feed(m,n,0)
    assert result[0] == (0,)*8


def test_monotonic_force_mapping_and_saturation():
    values=[]
    for force in (.5,1,2,4,8,10,25.5):
        m=Mapper()
        for n in range(40):
            levels,_=feed(m,n,force)
        values.append(levels[4])
    assert values == sorted(values)
    assert len(set(values[:5])) == 5
    assert values[-1] == 60


def test_hysteresis_and_no_lingering_contact_boost():
    m=Mapper()
    for n in range(20): feed(m,n,.3)
    assert not m.state[4].contact
    for n in range(20,40): feed(m,n,1)
    for n in range(40,60): feed(m,n,.3)
    assert m.state[4].contact
    assert m.state[4].cue == "pressure"


def test_budget_and_spatial_routing_conserve_resultant():
    c=MappingConfig(routes=(Route('finger',4,taxels=(0,)),Route('finger',5,taxels=(1,))))
    m=Mapper(c)
    for n in range(30):
        levels,details=feed(m,n,8,taxels=((0,0,1),(0,0,3)))
    assert details['channels']['4']['normal_n'] == pytest.approx(2)
    assert details['channels']['5']['normal_n'] == pytest.approx(6)
    assert levels[5]>levels[4]
    routes=tuple(Route(str(ch),ch) for ch in range(8))
    m=Mapper(MappingConfig(routes=routes))
    for n in range(50):
        for ch in range(8): m.ingest(Sample(str(ch),n,1+n*.02,(0,0,25)))
        levels,details=m.render(1+n*.02)
    assert sum(x*x for x in levels)<=7200
    assert details['budget_scale']<1


def test_stale_duplicate_future_invalid_and_gap():
    m=Mapper(); feed(m,0,2)
    with pytest.raises(TimeoutError): m.render(1.151)
    with pytest.raises(TimeoutError): m.render(.99)
    with pytest.raises(ValueError): feed(m,0,2)
    with pytest.raises(ValueError): m.ingest(Sample('finger',2,1.04,(0,0,float('nan'))))
    with pytest.raises(TimeoutError): feed(m,20,2)


@pytest.mark.parametrize('changes', [dict(gamma=0),dict(stale_s=.3),dict(cutoff_hz=float('inf')),
    dict(energy_budget=8000),dict(contact_off_n=.5),dict(shear_cue=1),
    dict(routes=(Route('finger',4),Route('finger',5))),
    dict(routes=(Route('finger',4,taxels=(0,1)),Route('finger',5,taxels=(1,2)))),
    dict(routes=(Route('finger',4,max_level=77),))])
def test_invalid_configuration(changes):
    with pytest.raises(ValueError): Mapper(replace(MappingConfig(),**changes))


def test_no_fake_shear_slip_claim_by_default():
    m=Mapper()
    for n in range(40): levels,details=feed(m,n,2,fx=(n%2)*10)
    assert details['channels']['4']['cue']=='pressure'


def test_signed_force_and_actual_com7_vector():
    raw=bytes.fromhex('aa550d000300fbf0030000030001000000ff')
    assert decode_response(raw,3,0x3f0,3,True)==b'\x00\x00\x00'
    with pytest.raises(ValueError): decode_response(raw,3,0x3f0,3)
    with pytest.raises(ValueError): decode_response(raw,1,0x3f0,3,True)
    with pytest.raises(ValueError): decode_response(raw,3,0x40e,3,True)
    assert vector(bytes((255,128,255))) == pytest.approx((-.1,-12.8,25.5))
    req=read_request(3,0x3f0,3)
    assert len(req)==14 and sum(req)%256==0
    for i in (0,2,4,6,7,11,13,14,17):
        corrupt=bytearray(raw); corrupt[i]^=1
        with pytest.raises(ValueError): decode_response(bytes(corrupt),3,0x3f0,3,True)


class FakeMotor:
    def __init__(self):
        self.info=SimpleNamespace(caps=SimpleNamespace(has=lambda bit:True),present_mask=0xf0)
        self.calls=[]; self.stops=0
    def get_status(self): return SimpleNamespace(active_mask=0)
    def tactile_frame(self,*args,**kwargs): self.calls.append((args,kwargs))
    def stop_all(self,*args): self.stops+=1


def test_session_no_lease_from_duplicate_render_and_latched_stale_stop():
    d=FakeMotor(); m=Mapper(); feed(m,0,5)
    clock=[1.]; s=Session(d,m,lambda:clock[0]); s.start(); s.step()
    assert d.calls[0][0][2]==(0,)*8
    assert s.step() is None and len(d.calls)==2
    clock[0]=1.2
    with pytest.raises(TimeoutError): s.step()
    assert d.stops==1 and not s.active and s.failed
    with pytest.raises(RuntimeError): s.start()


def test_old_firmware_rejected_and_ack_checked():
    d=FakeMotor(); d.info.caps.has=lambda bit:False
    with pytest.raises(RuntimeError): Session(d,Mapper()).start()
    class Transport:
        def request(self,cmd,payload,**kwargs):
            assert cmd==C.CMD_TACTILE_FRAME
            assert payload==struct.pack('<BBH8B',1,16,123,0,0,0,0,40,0,0,0)
            return SimpleNamespace(payload=b'bad')
    with pytest.raises(ValueError): AWTestDevice(Transport()).tactile_frame(16,123,(0,0,0,0,40,0,0,0))


def test_instant_ack_arrives_after_waiter_registration():
    from awtest.protocol.framing import Frame, FLAG_RESPONSE
    t=SerialTransport()
    class Serial:
        is_open=True
        def write(self,data):
            from awtest.protocol.framing import FrameParser
            request=FrameParser().feed(data)[0]
            response=Frame(flags=FLAG_RESPONSE,seq=request.seq,cmd=request.cmd,chan=request.chan,payload=b'ok')
            t._consume_binary(response.to_bytes())
        def flush(self): pass
    t._ser=Serial()
    assert t.request(C.CMD_PING,timeout=.01).payload==b'ok'


def test_cancellation_during_blocking_start_never_sends_update():
    from threading import Event
    from awtest.tactile.session import run_source
    stop=Event()
    class Source:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return Sample('finger',0,1.,(0,0,5))
    d=FakeMotor(); m=Mapper(); s=Session(d,m,lambda:1.)
    original=d.tactile_frame
    def frame(*args,**kwargs):
        original(*args,**kwargs)
        if kwargs.get('start'): stop.set()
    d.tactile_frame=frame
    run_source(Source(),s,stop)
    assert len(d.calls)==1 and d.calls[0][0][2]==(0,)*8
    assert d.stops==1 and not s.active


def test_gui_synchronous_stop_cancels_tactile_before_stop_command(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from awtest.ui.main_window import MainWindow
    app=QApplication.instance() or QApplication([])
    w=MainWindow()
    w.page_tactile._stop.clear()
    w.emergency_stop(synchronous=True)
    assert w.page_tactile._stop.is_set()
    w.page_tactile._stop.clear()
    w.close()
    assert w.page_tactile._stop.is_set()
    app.processEvents()


@pytest.mark.parametrize("pattern", ["pulse", "breath", "double"])
def test_patterns_bounded_release_and_deterministic_render(pattern):
    m=Mapper(MappingConfig(pattern=pattern))
    values=[]
    for n in range(300):
        levels,details=feed(m,n,6)
        assert m.render(1+n*.02)[0] == levels
        assert 0 <= levels[4] <= 60
        values.append(levels[4])
    assert max(values[50:])-min(values[50:]) > 15
    if pattern != 'breath': assert 0 in values[50:]
    for n in range(300,320): levels,_=feed(m,n,0)
    assert levels == (0,)*8


def test_pressure_pulse_tempo_increases():
    counts=[]
    for force in (1,10):
        m=Mapper(MappingConfig(pattern='pulse'))
        values=[feed(m,n,force)[0][4] for n in range(500)]
        counts.append(sum(a==0 and b>0 for a,b in zip(values,values[1:])))
    assert counts[1] > counts[0]*2
    assert 70 <= counts[1] <= 85


def test_invalid_pattern_rejected():
    with pytest.raises(ValueError): Mapper(MappingConfig(pattern='unknown'))


@pytest.mark.parametrize("pattern", ["pulse", "double"])
@pytest.mark.parametrize("force", [1., 10.])
def test_clicks_are_one_sample_not_long_duty_cycle(pattern,force):
    m=Mapper(MappingConfig(pattern=pattern))
    values=[feed(m,n,force)[0][4] for n in range(500)]
    assert values[0] >= 18  # no barely audible low-amplitude first sample
    assert any(values)
    assert all(not(a>0 and b>0) for a,b in zip(values,values[1:]))


def test_double_click_spacing_and_release_cancels_second():
    m=Mapper(MappingConfig(pattern='double'))
    clicks=[n for n in range(10) if feed(m,n,5)[0][4]>0]
    assert clicks[:2] == [0,4]
    m=Mapper(MappingConfig(pattern='double'))
    feed(m,0,5)
    for n in range(1,10): feed(m,n,0)
    assert m.state[4].second_at is None
    assert m.state[4].level == 0


def test_start_with_pressure_sends_first_click_even_after_slow_start(monkeypatch):
    from threading import Event
    import awtest.tactile.session as module
    stop=Event(); clock=[1.]
    class Source:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return Sample('finger',0,clock[0],(0,0,5))
    d=FakeMotor(); original=d.tactile_frame
    def frame(*args,**kwargs):
        original(*args,**kwargs)
        if kwargs.get('start'): clock[0]+=.025
    d.tactile_frame=frame
    session=Session(d,Mapper(MappingConfig(pattern='pulse')),lambda:clock[0])
    rows=[]
    def report(row): rows.append(row); stop.set()
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    module.run_source(Source(),session,stop,report)
    assert len(rows)==1 and rows[0]['levels'][4]>=18
    assert d.stops==1


def test_diagnostics_skip_on_pulse_and_failure_stops():
    d=FakeMotor(); m=Mapper(MappingConfig(pattern='pulse')); clock=[1.]
    m.ingest(Sample('finger',0,1.,(0,0,5)))
    session=Session(d,m,lambda:clock[0],telemetry=True)
    session.start(); assert session.step()['levels'][4]>0  # FakeMotor has no read API
    clock[0]=1.02; m.ingest(Sample('finger',1,1.02,(0,0,5)))
    with pytest.raises(AttributeError): session.step()
    assert session.failed and d.stops==1
