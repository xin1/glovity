"""TeleX stereo fisheye ArUco tracker (v3 stable runtime)."""

from telex.tracker.camera.calibration import load_fisheye_stereo_calibration
from telex.tracker.camera.dual_reader import DualCameraReader
from telex.tracker.geometry import mid_frame_from_left_mm, pose_in_frame
from telex.tracker.session import TrackerFrameResult, TrackerSession
from telex.tracker.types import HandRig, TrackerOutput, TrackerVision
from telex.tracker.vision.pipeline import estimate_tracker_vision
from telex.tracker.vision.rig import build_hand_rigs

__all__ = [
    "DualCameraReader",
    "HandRig",
    "TrackerFrameResult",
    "TrackerOutput",
    "TrackerSession",
    "TrackerVision",
    "build_hand_rigs",
    "estimate_tracker_vision",
    "load_fisheye_stereo_calibration",
    "mid_frame_from_left_mm",
    "pose_in_frame",
]
