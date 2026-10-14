"""
binarize.py - B&W scan functions.

`binarize`/`scan_image` purane (verbatim) pipeline hain - local tool aur parity
tests inhe as-is use karte hain. `binarize_document` naya shadow-corrected B&W
path hai jo `/scan` use karta hai.
"""
import cv2
import numpy as np

from .detection import scan_detection
from .enhancement import _odd, normalize_illumination
from .geometry import four_point_transform


def binarize(img, method="otsu"):
    """Warped page -> B&W. method: "otsu" (global) ya "adaptive" (local)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if method == "otsu":
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    if method == "adaptive":
        return cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )

    raise ValueError(f"unknown method: {method!r} (use 'otsu' ya 'adaptive')")


def scan_image(img, max_side=None, method="otsu"):
    """Poora scan ek call me: detect -> warp -> binarize.

    max_side sirf input ko bound karta hai; warped page full resolution rehta hai.
    Return: {"scan", "contour", "used_fallback"}.
    """
    if max_side:
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            factor = max_side / max(h, w)
            img = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)

    contour, used_fallback = scan_detection(img, return_fallback=True)

    warped = four_point_transform(img, contour.reshape(4, 2))
    scan = binarize(warped, method=method)

    return {"scan": scan, "contour": contour, "used_fallback": used_fallback}


def binarize_document(img, method="otsu"):
    """Shadow-corrected B&W: pehle illumination normalize, phir threshold.

    otsu = global, adaptive = local (block size image ke size ke hisaab se).
    Output inverted hota hai - white ink, black background. Strictly 0/255.
    """
    gray = cv2.cvtColor(normalize_illumination(img), cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)  # denoise, taaki Otsu speckle na pakde

    if method == "otsu":
        binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    elif method == "adaptive":
        block = _odd(max(15, min(gray.shape[:2]) // 24))
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 12
        )
    else:
        raise ValueError(f"unknown method: {method!r} (use 'otsu' ya 'adaptive')")

    return cv2.bitwise_not(binary)
