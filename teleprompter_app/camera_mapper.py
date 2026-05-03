import cv2
import logging

logger = logging.getLogger(__name__)

def detect_cameras(max_index=5):
    """
    Primary camera discovery using OpenCV with DSHOW only.
    DSHOW is typically faster for discovery on Windows.
    """
    cameras = []

    for i in range(max_index):
        # Use DSHOW exclusively for discovery to avoid backend conflict churn
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            cameras.append({
                "name": f"Camera {i}",
                "opencv_index": i
            })
            logger.info(f"Detected camera index {i} via DSHOW")
            cap.release()

    return cameras
