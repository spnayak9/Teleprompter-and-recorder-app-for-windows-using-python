from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OpenCVCamera:
    index: int


def detect_opencv_cameras(max_indices: int = 5) -> tuple[OpenCVCamera, ...]:
    """
    Detect OpenCV camera indices once.

    Rules:
    - DSHOW only.
    - Max index probing = 5.
    - No MSMF fallback loop.
    - Always release camera.
    """
    cameras: list[OpenCVCamera] = []

    for index in range(max_indices):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if not cap.isOpened():
                continue

            ok, _frame = cap.read()
            if not ok:
                continue

            cameras.append(OpenCVCamera(index=index))
            log.info("Detected camera index %s via DSHOW", index)
        finally:
            cap.release()

    return tuple(cameras)
