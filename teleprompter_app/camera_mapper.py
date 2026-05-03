import cv2


def detect_cameras(max_index=5):
    """
    Detects available camera indices for OpenCV.
    Returns a list of dictionaries with 'index' and 'name'.
    """
    cameras = []

    for i in range(max_index):
        # Use DSHOW on Windows for better compatibility/speed
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                cameras.append({
                    "index": i,
                    "name": f"Camera {i}"  # Replace later with FFmpeg mapping
                })
        cap.release()

    return cameras
