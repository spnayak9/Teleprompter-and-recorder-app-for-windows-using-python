"""Camera preview pipeline using OpenCV.

Only used for low-latency preview (not recording). This module exposes a
`Previewer` class that runs a background thread, captures frames from a
DirectShow camera via OpenCV, downsizes them for preview, and emits frames
via a callback. It also reports a realtime FPS estimate.
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None


FrameCallback = Callable[[object], None]  # numpy.ndarray expected
FPSCallback = Callable[[float], None]


@dataclass
class PreviewConfig:
    device_idx: int
    preview_size: Tuple[int, int] = (640, 360)
    backend: int = cv2.CAP_DSHOW if cv2 is not None else 0
    desired_fps: Optional[float] = None


class Previewer:
    def __init__(self, config: PreviewConfig, frame_callback: FrameCallback | None = None, fps_callback: FPSCallback | None = None):
        if cv2 is None:
            raise RuntimeError("OpenCV (cv2) is required for Previewer")
        self.config = config
        self.frame_callback = frame_callback
        self.fps_callback = fps_callback
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        cfg = self.config
        cap = None
        try:
            device_idx = cfg.device_idx
            cap = cv2.VideoCapture(device_idx, cfg.backend)
            if not cap.isOpened():
                raise RuntimeError(f"Could not open camera index {device_idx}")

            if cfg.desired_fps:
                cap.set(cv2.CAP_PROP_FPS, float(cfg.desired_fps))
            w, h = cfg.preview_size
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h))

            last_ts = time.time()
            frames = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.01)
                    continue
                frames += 1
                now = time.time()
                if now - last_ts >= 1.0:
                    fps = frames / (now - last_ts)
                    frames = 0
                    last_ts = now
                    if self.fps_callback:
                        try:
                            self.fps_callback(fps)
                        except Exception:
                            logger.exception("fps_callback failed")

                # resize to preview size to guarantee consistent display
                if frame.shape[1] != cfg.preview_size[0] or frame.shape[0] != cfg.preview_size[1]:
                    frame = cv2.resize(frame, cfg.preview_size)

                if self.frame_callback:
                    try:
                        self.frame_callback(frame)
                    except Exception:
                        logger.exception("frame_callback failed")

        except Exception:
            logger.exception("Previewer failed to run")
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass


__all__ = ["PreviewConfig", "Previewer"]
