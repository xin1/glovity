"""Find-GCP 3x3 ArUco marker 检测。

字典与检测参数对齐 ``TeleX_Tracking_Marker/track_aruco_v0.py``：
使用 ``cv2.aruco.extendDictionary(32, 3)`` 生成的自定义 3x3 字典，检测参数使用
子像素角点细化等设置。不再扫描 20 种预定义字典，只用这一个字典。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from packaging import version as pkg_version

from telex.tracker.marker.dictionary import make_findgcp_dictionary

_OPENCV_NEW_ARUCO = pkg_version.parse(cv2.__version__) >= pkg_version.parse("4.7.0")


@dataclass(frozen=True)
class DictionarySpec:
    """标记检测所用字典的标识（仅用于日志/追踪对齐）。"""

    name: str


@dataclass(frozen=True)
class SelectedMarker:
    spec: DictionarySpec
    marker_id: int
    corners: np.ndarray


FINDGCP_SPEC = DictionarySpec(name="FINDGCP_3X3")


def build_detector_parameters():
    aruco = cv2.aruco
    params = aruco.DetectorParameters() if _OPENCV_NEW_ARUCO else aruco.DetectorParameters_create()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 33
    params.adaptiveThreshWinSizeStep = 6
    params.minMarkerPerimeterRate = 0.03
    params.polygonalApproxAccuracyRate = 0.05
    params.perspectiveRemoveIgnoredMarginPerCell = 0.13
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    return params


class FindGcpDetector:
    """封装 Find-GCP 3x3 字典的检测器，兼容新旧 OpenCV aruco API。"""

    def __init__(self) -> None:
        self.dictionary = make_findgcp_dictionary()
        self.parameters = build_detector_parameters()
        self._detector = (
            cv2.aruco.ArucoDetector(self.dictionary, self.parameters)
            if _OPENCV_NEW_ARUCO
            else None
        )

    def detect(self, image: np.ndarray):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if _OPENCV_NEW_ARUCO:
            corners, ids, rejected = self._detector.detectMarkers(gray)
        else:
            corners, ids, rejected = cv2.aruco.detectMarkers(
                gray, self.dictionary, parameters=self.parameters
            )
        return corners, ids, rejected

    def detect_targets(
        self, image: np.ndarray, target_ids: set[int]
    ) -> list[SelectedMarker]:
        corners, ids, _rejected = self.detect(image)
        return select_target_markers(corners, ids, target_ids)


def select_target_markers(
    corners,
    ids,
    target_ids: Optional[set[int]] = None,
) -> list[SelectedMarker]:
    if ids is None or len(ids) == 0:
        return []
    flat_ids = [int(v) for v in np.asarray(ids).reshape(-1)]
    selected: list[SelectedMarker] = []
    seen: set[int] = set()
    for marker_corners, marker_id in zip(corners, flat_ids):
        if target_ids is not None and marker_id not in target_ids:
            continue
        if marker_id in seen:
            continue
        seen.add(marker_id)
        selected.append(
            SelectedMarker(
                spec=FINDGCP_SPEC,
                marker_id=marker_id,
                corners=np.asarray(marker_corners, dtype=np.float32).reshape(4, 2),
            )
        )
    return selected
