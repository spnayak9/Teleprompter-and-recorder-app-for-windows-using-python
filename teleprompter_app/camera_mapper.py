import cv2
import logging

logger = logging.getLogger(__name__)

def detect_cameras(max_index=10):
    """
    Primary camera discovery using OpenCV.
    Returns a list of CameraProfile-compatible dictionaries.
    """
    cameras = []

    for i in range(max_index):
        # Use DSHOW on Windows as it's the most reliable for mapping to FFmpeg names
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            # Basic validation: can we actually read a frame?
            ret, _ = cap.read()
            if ret:
                # On Windows with CAP_DSHOW, we try to get a name if possible
                # (Though standard OpenCV doesn't expose it easily, some builds do)
                name = f"Camera {i}"
                
                # We could try to correlate with a more descriptive name later
                cameras.append({
                    "name": name,
                    "opencv_index": i,
                    "device_path": None # To be filled by FFmpeg correlation if possible
                })
                logger.info(f"Detected camera: {name} at index {i}")
        cap.release()

    return cameras
