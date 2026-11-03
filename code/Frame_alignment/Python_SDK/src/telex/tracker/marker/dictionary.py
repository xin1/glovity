"""Find-GCP 3x3 ArUco dictionary (runtime only)."""
from __future__ import annotations

import cv2

DICT_SIZE = 32
PAYLOAD_SIZE = 3


def make_findgcp_dictionary():
    aruco = cv2.aruco
    if hasattr(aruco, "extendDictionary"):
        return aruco.extendDictionary(DICT_SIZE, PAYLOAD_SIZE)
    if hasattr(aruco, "Dictionary_create"):
        return aruco.Dictionary_create(DICT_SIZE, PAYLOAD_SIZE)
    raise RuntimeError(
        "OpenCV build missing extendDictionary/Dictionary_create; "
        "install opencv-contrib-python."
    )
