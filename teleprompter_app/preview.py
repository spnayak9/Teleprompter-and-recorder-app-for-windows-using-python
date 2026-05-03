import cv2
import time
import logging
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)

class PreviewWorker(QThread):
    """
    QThread-based worker for camera preview.
    Emits frame_ready signal with QImage for safe UI updates.
    """
    frame_ready = Signal(QImage)
    fps_ready = Signal(float)

    def __init__(self, camera_index: int, width=None, height=None):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self._running = False

    def run(self):
        # Use DSHOW exclusively for preview to match discovery and avoid backend conflicts
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)

        if self.width and self.height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        self._running = True
        frame_count = 0
        start_time = time.time()

        logger.info(f"Starting preview worker for camera index {self.camera_index}")

        try:
            while self._running:
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                # Convert BGR (OpenCV) to RGB (Qt)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                h, w, ch = frame.shape
                bytes_per_line = ch * w

                # Create QImage directly from the frame data
                # Important: QImage doesn't copy the data by default, 
                # but since we are emitting it and then looping, we might need a copy
                # if the receiver doesn't process it immediately. 
                # However, QPixmap.fromImage() copies the data.
                qt_image = QImage(
                    frame.data, w, h, bytes_per_line, QImage.Format_RGB888
                ).copy() # Use .copy() to ensure the data is safe for the signal

                self.frame_ready.emit(qt_image)

                # FPS calculation
                frame_count += 1
                elapsed = time.time() - start_time
                if elapsed >= 1.0:
                    fps = frame_count / elapsed
                    self.fps_ready.emit(fps)
                    frame_count = 0
                    start_time = time.time()
        except Exception as e:
            logger.error(f"Error in PreviewWorker: {e}")
        finally:
            cap.release()
            logger.info("Preview worker stopped and camera released")

    def stop(self):
        self._running = False
        self.wait()

__all__ = ["PreviewWorker"]
