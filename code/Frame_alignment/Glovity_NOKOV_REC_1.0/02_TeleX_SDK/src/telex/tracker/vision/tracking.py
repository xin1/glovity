from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

import numpy as np

from telex.tracker.marker.detection import DictionarySpec, SelectedMarker


MarkerObservationStatus = Literal["fresh", "held", "rejected"]


def marker_area_px(corners: np.ndarray) -> float:
    pts = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    x = pts[:, 0]
    y = pts[:, 1]
    return float(
        0.5
        * abs(
            np.dot(x, np.roll(y, -1))
            - np.dot(y, np.roll(x, -1))
        )
    )


def marker_min_side_px(corners: np.ndarray) -> float:
    pts = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    edges = np.roll(pts, -1, axis=0) - pts
    return float(np.min(np.linalg.norm(edges, axis=1)))


@dataclass(frozen=True)
class MarkerTrackObservation:
    """A marker observation annotated with its temporal validity.

    Only ``status == "fresh"`` is a real observation from the current frame and
    may be used by PnP. ``held`` is display/debug state only. ``rejected`` means
    the detector returned the marker in the current frame, but it failed one of
    the temporal/quality gates.
    """

    marker: SelectedMarker
    status: MarkerObservationStatus
    age_frames: int
    area_px: float
    min_side_px: float
    max_corner_shift_px: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"fresh", "held", "rejected"}:
            raise ValueError(f"unknown marker observation status: {self.status}")
        if int(self.age_frames) < 0:
            raise ValueError("age_frames cannot be negative")
        for name, value in (
            ("area_px", self.area_px),
            ("min_side_px", self.min_side_px),
            ("max_corner_shift_px", self.max_corner_shift_px),
        ):
            if not np.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class MultiMarkerUpdate:
    fresh: tuple[MarkerTrackObservation, ...]
    held: tuple[MarkerTrackObservation, ...]
    rejected: tuple[MarkerTrackObservation, ...]

    @property
    def fresh_markers(self) -> list[SelectedMarker]:
        return [observation.marker for observation in self.fresh]

    @property
    def held_markers(self) -> list[SelectedMarker]:
        return [observation.marker for observation in self.held]

    @property
    def display_observations(self) -> tuple[MarkerTrackObservation, ...]:
        return self.fresh + self.held

    def __iter__(self) -> Iterator[SelectedMarker]:
        """Backward-compatible iteration exposes only current-frame markers."""
        return iter(self.fresh_markers)

    def __len__(self) -> int:
        return len(self.fresh)


@dataclass
class _MarkerTrack:
    spec: DictionarySpec
    marker_id: int
    hold_frames: int
    smooth_window: int
    max_corner_shift_px: float
    min_keep_area_px: float
    adaptive_smooth_shift_px: float
    history: "deque[np.ndarray]" = field(init=False)
    misses: int = 0
    active: bool = False
    enter_count: int = 0
    last_area_px: float = 0.0
    last_min_side_px: float = 0.0
    last_shift_px: float = 0.0
    last_rejection_reason: str = ""

    def __post_init__(self) -> None:
        self.history = deque(maxlen=max(1, int(self.smooth_window)))

    def reference_corners(self) -> Optional[np.ndarray]:
        # Compare motion against the latest accepted frame, not a temporal mean.
        # A mean lags behind sustained hand motion and can falsely reject a valid
        # marker after several consecutive moving frames.
        return self.latest_corners()

    def latest_corners(self) -> Optional[np.ndarray]:
        if not self.history:
            return None
        return np.asarray(self.history[-1], dtype=np.float32).copy()

    def observe(self, marker: SelectedMarker) -> tuple[bool, str]:
        corners = np.asarray(marker.corners, dtype=np.float32).reshape(4, 2)
        area = marker_area_px(corners)
        min_side = marker_min_side_px(corners)
        self.last_area_px = area
        self.last_min_side_px = min_side
        self.last_shift_px = 0.0

        if self.active and area < self.min_keep_area_px:
            self.misses += 1
            self.last_rejection_reason = "area-below-keep-threshold"
            return False, self.last_rejection_reason

        reference = self.reference_corners()
        if reference is not None:
            shift = float(
                np.max(np.linalg.norm(corners - reference, axis=1))
            )
            self.last_shift_px = shift
            if shift > self.max_corner_shift_px:
                self.misses += 1
                self.last_rejection_reason = "corner-shift-too-large"
                return False, self.last_rejection_reason

        self.spec = marker.spec
        self.history.append(corners.copy())
        self.misses = 0
        self.active = True
        self.enter_count = 0
        self.last_rejection_reason = ""
        return True, ""

    def miss(self, reason: str = "not-detected") -> None:
        self.misses += 1
        self.last_rejection_reason = reason

    def alive(self) -> bool:
        return (
            self.active
            and self.misses <= self.hold_frames
            and bool(self.history)
        )

    def fresh_observation(self, marker: SelectedMarker) -> MarkerTrackObservation:
        raw_corners = np.asarray(marker.corners, dtype=np.float32).reshape(4, 2)
        corners = raw_corners
        if (
            self.smooth_window > 1
            and len(self.history) > 1
            and self.last_shift_px <= self.adaptive_smooth_shift_px
        ):
            # Planar single-marker PnP is especially sensitive to sub-pixel
            # corner noise along depth. Use only a short history while the
            # marker is nearly still; fast image-space motion remains raw.
            corners = np.mean(np.stack(self.history), axis=0).astype(np.float32)
        fresh_marker = SelectedMarker(
            marker.spec,
            int(marker.marker_id),
            corners.copy(),
        )
        return MarkerTrackObservation(
            marker=fresh_marker,
            status="fresh",
            age_frames=0,
            area_px=marker_area_px(raw_corners),
            min_side_px=marker_min_side_px(raw_corners),
            max_corner_shift_px=self.last_shift_px,
        )

    def rejected_observation(
        self, marker: SelectedMarker, reason: str
    ) -> MarkerTrackObservation:
        corners = np.asarray(marker.corners, dtype=np.float32).reshape(4, 2)
        rejected_marker = SelectedMarker(
            marker.spec,
            int(marker.marker_id),
            corners.copy(),
        )
        return MarkerTrackObservation(
            marker=rejected_marker,
            status="rejected",
            age_frames=max(1, self.misses),
            area_px=marker_area_px(corners),
            min_side_px=marker_min_side_px(corners),
            max_corner_shift_px=self.last_shift_px,
            reason=reason,
        )

    def held_observation(self) -> MarkerTrackObservation:
        corners = self.latest_corners()
        if corners is None:
            raise RuntimeError("cannot create held observation without corners")
        held_marker = SelectedMarker(
            self.spec,
            self.marker_id,
            corners,
        )
        return MarkerTrackObservation(
            marker=held_marker,
            status="held",
            age_frames=max(1, self.misses),
            area_px=marker_area_px(corners),
            min_side_px=marker_min_side_px(corners),
            max_corner_shift_px=self.last_shift_px,
            reason=self.last_rejection_reason,
        )


class MultiMarkerTracker:
    """Temporal marker bookkeeping without stale-corner PnP contamination.

    ``fresh_markers`` contains only detections accepted in the current frame.
    Held markers remain available for visualization and diagnostics, but are
    deliberately excluded from pose estimation.
    """

    def __init__(
        self,
        *,
        hold_frames: int = 4,
        smooth_window: int = 3,
        max_corner_shift_px: float = 80.0,
        min_enter_area_px: float = 0.0,
        min_keep_area_px: float = 0.0,
        enter_frames: int = 1,
        adaptive_smooth_shift_px: float = 5.0,
    ) -> None:
        if int(hold_frames) < 0:
            raise ValueError("hold_frames cannot be negative")
        if int(smooth_window) < 1:
            raise ValueError("smooth_window must be >= 1")
        if float(max_corner_shift_px) < 0.0:
            raise ValueError("max_corner_shift_px cannot be negative")
        if float(min_enter_area_px) < 0.0 or float(min_keep_area_px) < 0.0:
            raise ValueError("marker area thresholds cannot be negative")
        if float(adaptive_smooth_shift_px) < 0.0:
            raise ValueError("adaptive_smooth_shift_px cannot be negative")

        self.hold_frames = int(hold_frames)
        self.smooth_window = int(smooth_window)
        self.max_corner_shift_px = float(max_corner_shift_px)
        self.min_enter_area_px = float(min_enter_area_px)
        self.min_keep_area_px = float(min_keep_area_px)
        self.enter_frames = max(1, int(enter_frames))
        self.adaptive_smooth_shift_px = float(adaptive_smooth_shift_px)
        self._tracks: dict[int, _MarkerTrack] = {}

    def update(self, markers: list[SelectedMarker]) -> MultiMarkerUpdate:
        seen: set[int] = set()
        fresh: list[MarkerTrackObservation] = []
        rejected: list[MarkerTrackObservation] = []

        for marker in markers:
            marker_id = int(marker.marker_id)
            if marker_id in seen:
                raise ValueError(f"duplicate marker ID in one frame: {marker_id}")
            seen.add(marker_id)

            if marker_id not in self._tracks:
                self._tracks[marker_id] = _MarkerTrack(
                    spec=marker.spec,
                    marker_id=marker_id,
                    hold_frames=self.hold_frames,
                    smooth_window=self.smooth_window,
                    max_corner_shift_px=self.max_corner_shift_px,
                    min_keep_area_px=self.min_keep_area_px,
                    adaptive_smooth_shift_px=self.adaptive_smooth_shift_px,
                )

            track = self._tracks[marker_id]
            if not track.active:
                area = marker_area_px(marker.corners)
                if area >= self.min_enter_area_px:
                    track.enter_count += 1
                else:
                    track.enter_count = 0
                if track.enter_count < self.enter_frames:
                    corners = np.asarray(
                        marker.corners, dtype=np.float32
                    ).reshape(4, 2)
                    rejected.append(
                        MarkerTrackObservation(
                            marker=SelectedMarker(
                                marker.spec,
                                marker_id,
                                corners.copy(),
                            ),
                            status="rejected",
                            age_frames=0,
                            area_px=marker_area_px(corners),
                            min_side_px=marker_min_side_px(corners),
                            reason="waiting-for-enter-confirmation",
                        )
                    )
                    continue

            accepted, reason = track.observe(marker)
            if accepted:
                fresh.append(track.fresh_observation(marker))
            else:
                rejected.append(track.rejected_observation(marker, reason))

        for marker_id, track in list(self._tracks.items()):
            if marker_id not in seen:
                if track.active:
                    track.miss()
                else:
                    track.enter_count = 0
            if track.active and not track.alive():
                del self._tracks[marker_id]

        fresh_ids = {observation.marker.marker_id for observation in fresh}
        held = [
            track.held_observation()
            for marker_id, track in sorted(self._tracks.items())
            if track.alive()
            and marker_id not in fresh_ids
            and track.misses > 0
        ]

        return MultiMarkerUpdate(
            fresh=tuple(sorted(fresh, key=lambda item: item.marker.marker_id)),
            held=tuple(sorted(held, key=lambda item: item.marker.marker_id)),
            rejected=tuple(
                sorted(rejected, key=lambda item: item.marker.marker_id)
            ),
        )


def fresh_corner_motion_px(
    update: MultiMarkerUpdate,
    marker_ids,
) -> float:
    """Return the largest current-frame corner displacement for a rig."""
    configured = {int(marker_id) for marker_id in marker_ids}
    return max(
        (
            observation.max_corner_shift_px
            for observation in update.fresh
            if observation.marker.marker_id in configured
        ),
        default=0.0,
    )
