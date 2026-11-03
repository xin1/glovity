"""Calibratable force coding; driver levels are NOT physical acceleration.

Taxels allocate the sensor's resultant force spatially; their magnitudes are
never summed and claimed to be a calibrated resultant. Routes are explicit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class Sample:
    sensor: str
    sequence: int
    acquired: float                 # local monotonic time, not wall clock
    force: tuple[float, float, float]  # Fx,Fy,Fz in N, Fz positive into sensor
    taxels: tuple[tuple[float, float, float], ...] = ()

    def validate(self):
        if not isinstance(self.sensor, str) or not self.sensor:
            raise ValueError("sensor ID required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be a nonnegative integer")
        finite(self.acquired, "acquired")
        for vector in (self.force, *self.taxels):
            if len(vector) != 3:
                raise ValueError("force vectors must have three axes")
            for value in vector:
                finite(value, "force")


@dataclass(frozen=True)
class Route:
    sensor: str
    channel: int
    taxels: tuple[int, ...] = ()     # empty = whole sensor, otherwise zero-based ROI
    baseline: tuple[float, float, float] = (0., 0., 0.)
    force_full_n: float = 10.0
    min_level: int = 18
    max_level: int = 60


@dataclass(frozen=True)
class MappingConfig:
    routes: tuple[Route, ...] = (Route("finger", 4),)
    contact_on_n: float = .4
    contact_off_n: float = .2
    cutoff_hz: float = 15.0
    gamma: float = .6
    stale_s: float = .150
    slew_per_s: float = 500.
    onset_boost: float = 8.
    pattern: str = "steady"        # steady, pulse, breath, double
    shear_cue: bool = False          # opt-in heuristic, NOT confirmed slip
    shear_ratio: float = .6
    shear_rate_n_s: float = 1.0
    energy_budget: float = 7200.0    # sum(level**2) <= 2 * 60**2; not amperes

    def validate(self):
        if not self.routes or len(self.routes) > 8:
            raise ValueError("need 1..8 routes")
        if self.pattern not in ("steady", "pulse", "breath", "double"):
            raise ValueError("unknown tactile pattern")
        channels = set()
        regions = {}
        for r in self.routes:
            if not r.sensor or type(r.channel) is not int or not 0 <= r.channel < 8 or r.channel in channels:
                raise ValueError("routes need unique channels 0..7 and sensor IDs")
            channels.add(r.channel)
            if len(r.baseline) != 3:
                raise ValueError("baseline requires 3 axes")
            for x in r.baseline:
                finite(x, "baseline")
            if not 0 < finite(r.force_full_n, "force_full_n") <= 1000:
                raise ValueError("invalid force scale")
            if any(type(x) is not int for x in (r.min_level, r.max_level)) or not 0 <= r.min_level <= r.max_level <= 60:
                raise ValueError("levels require 0 <= min <= max <= 60")
            if any(type(x) is not int or x < 0 for x in r.taxels) or len(set(r.taxels)) != len(r.taxels):
                raise ValueError("invalid taxel indexes")
            old = regions.setdefault(r.sensor, [])
            if old and (not r.taxels or any(not region or set(region) & set(r.taxels) for region in old)):
                raise ValueError("same-sensor routes require disjoint explicit taxel regions")
            old.append(r.taxels)
        for name in ("contact_on_n", "contact_off_n", "cutoff_hz", "gamma", "stale_s", "slew_per_s", "onset_boost", "shear_ratio", "shear_rate_n_s", "energy_budget"):
            finite(getattr(self, name), name)
        if not 0 <= self.contact_off_n < self.contact_on_n < min(r.force_full_n for r in self.routes):
            raise ValueError("contact thresholds must be below full force")
        if not .1 <= self.cutoff_hz <= 40 or not .1 <= self.gamma <= 3:
            raise ValueError("invalid filter/gamma")
        if not .02 <= self.stale_s <= .15 or not 1 <= self.slew_per_s <= 2000:
            raise ValueError("invalid freshness/slew bound")
        if not 0 <= self.onset_boost <= 20 or not 0 < self.energy_budget <= 7200:
            raise ValueError("invalid output budget")
        if type(self.shear_cue) is not bool or self.shear_ratio <= 0 or self.shear_rate_n_s <= 0:
            raise ValueError("invalid shear cue settings")


@dataclass
class State:
    sequence: int = -1
    acquired: float | None = None
    normal: float = 0.
    shear: float = 0.
    contact: bool = False
    onset: float = 0.
    risk_since: float | None = None
    level: float = 0.
    cue: str = "none"
    phase: float = 0.
    gain: float = 1.
    tempo: float = 0.
    raw_normal: float = 0.
    click_until: float = 0.
    second_at: float | None = None


class Mapper:
    def __init__(self, config=MappingConfig()):
        config.validate()
        self.config = config
        self.state = {r.channel: State() for r in config.routes}
        self.samples = {}

    @property
    def mask(self):
        return sum(1 << r.channel for r in self.config.routes)

    def ingest(self, sample: Sample):
        sample.validate()
        if sample.sensor not in {r.sensor for r in self.config.routes}:
            raise ValueError("unmapped sensor")
        prev = self.samples.get(sample.sensor)
        if prev and (sample.sequence <= prev.sequence or sample.acquired <= prev.acquired):
            raise ValueError("duplicate or out-of-order tactile sample")
        self.samples[sample.sensor] = sample

    def render(self, now):
        now = finite(now, "now")
        c = self.config
        levels = [0] * 8
        details = {}
        for r in c.routes:
            s = self.samples.get(r.sensor)
            if s is None or not 0 <= now - s.acquired <= c.stale_s:
                raise TimeoutError(f"tactile data missing/stale: {r.sensor}")
            st = self.state[r.channel]
            if s.sequence != st.sequence:
                dt = .02 if st.acquired is None else s.acquired - st.acquired
                if dt <= 0 or dt > c.stale_s:
                    raise TimeoutError("tactile sample gap exceeds freshness bound")
                fx, fy, fz = (s.force[i] - r.baseline[i] for i in range(3))
                fz = max(0., fz)
                if r.taxels:
                    if not s.taxels or max(r.taxels) >= len(s.taxels):
                        raise ValueError("taxel region does not match received sensor")
                    total = sum(max(0., v[2]) for v in s.taxels)
                    share = sum(max(0., s.taxels[i][2]) for i in r.taxels) / total if total > 0 else 0.
                    fx, fy, fz = fx * share, fy * share, fz * share
                st.raw_normal = fz
                alpha = -math.expm1(-2 * math.pi * c.cutoff_hz * dt)
                previous_shear = st.shear
                st.normal += alpha * (fz - st.normal)
                st.shear += alpha * (math.hypot(fx, fy) - st.shear)
                was_contact = st.contact
                st.contact = st.normal > (c.contact_off_n if st.contact else c.contact_on_n)
                if st.contact and not was_contact:
                    st.onset = s.acquired
                    st.phase = 0.
                risk = (c.shear_cue and st.contact and
                        st.shear / max(st.normal, c.contact_on_n) > c.shear_ratio and
                        abs(st.shear - previous_shear) / dt > c.shear_rate_n_s)
                st.risk_since = (st.risk_since if st.risk_since is not None else s.acquired) if risk else None
                st.sequence, st.acquired = s.sequence, s.acquired
                st.cue = "pressure" if st.contact else "none"
                target = 0.
                if st.contact:
                    u = min(1., max(0., (st.normal - c.contact_off_n) / (r.force_full_n - c.contact_off_n)))
                    target = r.min_level + (r.max_level - r.min_level) * u ** c.gamma
                    st.tempo = (1.5 + 6.5*u) if c.pattern == "pulse" else (1. + 2.*u if c.pattern == "double" else .5 + u)
                    previous_phase = st.phase
                    if was_contact:
                        st.phase = (st.phase + st.tempo * dt) % 1.
                    new_beat = not was_contact or st.phase < previous_phase
                    st.gain = 1.
                    if c.pattern in ("pulse", "double"):
                        if new_beat:
                            st.click_until = s.acquired + .018
                            st.second_at = s.acquired + .080 if c.pattern == "double" else None
                        elif st.second_at is not None and s.acquired >= st.second_at:
                            st.click_until = s.acquired + .018
                            st.second_at = None
                        st.gain = 1. if s.acquired < st.click_until else 0.
                    elif c.pattern == "breath":
                        st.gain = .4 + .6 * (.5 + .5 * math.cos(2*math.pi*st.phase))
                    age = s.acquired - st.onset
                    if age < .1:
                        target += c.onset_boost * math.exp(-age / .035)
                        st.cue = "contact"
                    if st.risk_since is not None and s.acquired - st.risk_since >= .03:
                        target += 6 * (.5 + .5 * math.sin(2 * math.pi * 8 * (s.acquired - st.risk_since)))
                        st.cue = "shear-risk"
                    target = min(r.max_level, target)
                    if c.pattern in ("pulse", "double"):
                        st.level = target  # sharp attack; peak/budget clamps still apply
                    else:
                        st.level += max(-c.slew_per_s * dt, min(c.slew_per_s * dt, target - st.level))
                else:
                    st.level = 0.       # no release tail after contact-off
                    st.risk_since = None
                    st.second_at = None
                    st.click_until = 0.
            levels[r.channel] = int(max(0., min(r.max_level, st.level * st.gain)))
            details[str(r.channel)] = {"raw_normal_n": st.raw_normal, "tempo_hz": st.tempo if st.contact and c.pattern != "steady" else 0., "pattern": c.pattern, "normal_n": st.normal, "shear_n": st.shear,
                                      "cue": st.cue, "age_ms": (now - s.acquired) * 1000}
        energy = sum(x*x for x in levels)
        scale = min(1., math.sqrt(c.energy_budget / energy)) if energy else 1.
        levels = tuple(int(x * scale) for x in levels)
        return levels, {"channels": details, "budget_scale": scale}
