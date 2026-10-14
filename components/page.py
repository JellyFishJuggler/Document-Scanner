"""
page.py - warped page taiyaar karna (auto-detect ya client corners).
"""
import cv2
import numpy as np

from .detection import scan_detection
from .geometry import four_point_transform, order_corners, scale_corners


def prepare_page(img, max_side=None, corners=None):
    """Page ko warp karo. corners None ho to auto-detect, warna unhi par warp.

    Return dict: page (colour warped), contour, detected, corners_norm, scale.
    """
    h, w = img.shape[:2]
    scale = 1.0

    work = img
    if max_side and max(h, w) > max_side:
        scale = max_side / max(h, w)
        work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if corners is None:
        contour, used_fallback = scan_detection(work, return_fallback=True)
        contour_px = np.asarray(contour, dtype=np.float32).reshape(4, 2)
        detected = not used_fallback
        # contour resized `work` par hai; source frame ke normalized corners chahiye.
        contour_src = contour_px / scale
        corners_norm = contour_src / np.array([w, h], dtype=np.float32)
    else:
        contour_px = scale_corners(corners, work.shape[1], work.shape[0])
        corners_norm = np.array(
            order_corners(np.asarray(corners, dtype=np.float32).reshape(4, 2)),
            dtype=np.float32,
        )
        detected = True

    page = four_point_transform(work, contour_px)

    return {
        "page": page,
        "contour": contour_px,
        "detected": detected,
        "corners_norm": corners_norm,
        "scale": scale,
    }
