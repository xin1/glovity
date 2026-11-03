"""Fit glove-local microseconds onto a common NOKOV time axis."""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics


@dataclass(frozen=True)
class SyncPoint:
    glove_us: int
    nokov_us: int


@dataclass(frozen=True)
class ClockModel:
    scale: float
    offset_us: float
    rms_error_us: float
    max_error_us: float
    point_count: int

    def to_nokov(self, glove_us: int | float) -> float:
        return self.scale * glove_us + self.offset_us

    def to_glove(self, nokov_us: int | float) -> float:
        if self.scale == 0:
            raise ZeroDivisionError("clock scale is zero")
        return (nokov_us - self.offset_us) / self.scale

    @property
    def drift_ppm(self) -> float:
        return (self.scale - 1.0) * 1_000_000.0


def _least_squares(points: list[SyncPoint]) -> tuple[float, float]:
    if len(points) == 1:
        point = points[0]
        return 1.0, float(point.nokov_us - point.glove_us)
    mean_x = sum(point.glove_us for point in points) / len(points)
    mean_y = sum(point.nokov_us for point in points) / len(points)
    denominator = sum((point.glove_us - mean_x) ** 2 for point in points)
    if denominator == 0:
        raise ValueError("sync points must contain at least two distinct glove timestamps")
    scale = sum((p.glove_us - mean_x) * (p.nokov_us - mean_y) for p in points) / denominator
    return scale, mean_y - scale * mean_x


def fit_clock_model(
    points: list[SyncPoint], *, reject_outliers: bool = True, max_iterations: int = 4
) -> ClockModel:
    """Fit t_nokov = scale*t_glove + offset, with optional MAD rejection."""
    if not points:
        raise ValueError("at least one sync point is required")
    active = list(points)
    for _ in range(max_iterations if reject_outliers else 1):
        scale, offset = _least_squares(active)
        if len(active) < 4 or not reject_outliers:
            break
        residuals = [point.nokov_us - (scale * point.glove_us + offset) for point in active]
        center = statistics.median(residuals)
        mad = statistics.median(abs(value - center) for value in residuals)
        threshold = max(100.0, 4.5 * 1.4826 * mad)
        retained = [p for p, r in zip(active, residuals) if abs(r - center) <= threshold]
        if len(retained) < 2 or len(retained) == len(active):
            break
        active = retained
    scale, offset = _least_squares(active)
    errors = [point.nokov_us - (scale * point.glove_us + offset) for point in active]
    rms = math.sqrt(sum(value * value for value in errors) / len(errors))
    return ClockModel(scale, offset, rms, max(abs(value) for value in errors), len(active))
