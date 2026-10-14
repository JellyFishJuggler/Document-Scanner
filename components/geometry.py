"""
geometry.py - corner ordering, perspective warp, resize aur corner scaling.
"""
import cv2
import numpy as np


def order_corners(pts):
    """Chaar corners ko TL/TR/BR/BL me order karo (client kisi bhi order me bheje)."""
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]


def four_point_transform(img, pts):
    """Warped page nikalo: corners order karke perspective transform lagao."""
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)

    tl, tr, br, bl = order_corners(pts)

    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))

    src = np.float32([tl, tr, br, bl])
    dst = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, matrix, (width, height))


def resize_to_fit(img, max_w=800, max_h=600):
    """Sirf display/preview ke liye resize. Scan ke liye mat use karo - text blur hota hai."""
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    return cv2.resize(img, None, fx=scale, fy=scale)


def scale_corners(corners, width, height):
    """Normalized corners ko pixel coords me badlo.

    `copy=True` zaroori hai: in-place multiply caller ke array ko corrupt kar deta hai.
    """
    pts = np.array(corners, dtype=np.float32, copy=True).reshape(4, 2)
    pts[:, 0] *= width
    pts[:, 1] *= height
    return np.clip(pts, 0, [width - 1, height - 1])
