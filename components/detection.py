"""
detection.py - camera frame me document ka contour dhoondhna.
"""
import cv2
import numpy as np


def scan_detection(img, scale=0.4, return_fallback=False):
    """Frame me sabse bada 4-corner document dhoondho.

    Detection chhoti copy (0.4x) par hoti hai, coordinates wapas original size
    me scale kiye jaate hain. `return_fallback=True` par (contour, used_fallback)
    milta hai; warna sirf contour (main.py ka purana behaviour).
    """
    h, w = img.shape[:2]

    doc_contour = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.int32)

    small = cv2.resize(img, None, fx=scale, fy=scale)
    sh, sw = small.shape[:2]

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, threshold = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = np.ones((9, 9), np.uint8)
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel)
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:3]:
        area = cv2.contourArea(contour)
        if not (0.1 * sh * sw < area < 0.9 * sh * sw):  # logo / poora frame ignore
            continue

        hull = cv2.convexHull(contour)
        peri = cv2.arcLength(hull, True)
        for eps in (0.015, 0.02, 0.03, 0.04, 0.05):
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4:
                found = (approx / scale).astype(np.int32)
                return (found, False) if return_fallback else found

    return (doc_contour, True) if return_fallback else doc_contour
