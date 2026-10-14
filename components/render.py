"""
render.py - `/scan` ka output router (colour / grayscale / B&W).
"""
import cv2

from .binarize import binarize_document
from .enhancement import enhance_document, normalize_illumination


def render_scan(page, method="otsu"):
    """Warped page -> scan output. `/scan` ka single entry point.

    method: enhanced (colour, 3ch), grayscale (1ch), otsu/adaptive (B&W, 1ch).
    Har branch ke liye simple fallback hai taaki corrupt output na mile.
    Return: {"image": ndarray, "channels": int}.
    """
    if method == "enhanced":
        try:
            return {"image": enhance_document(page), "channels": 3}
        except Exception:
            try:
                return {"image": normalize_illumination(page), "channels": 3}
            except Exception:
                return {"image": page, "channels": 3}

    if method in ("otsu", "adaptive"):
        try:
            return {"image": binarize_document(page, method), "channels": 1}
        except Exception:
            gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
            binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            return {"image": binary, "channels": 1}

    if method == "grayscale":
        try:
            flat = normalize_illumination(page)
        except Exception:
            flat = page
        return {"image": cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY), "channels": 1}

    raise ValueError(
        f"unknown method: {method!r} (use 'enhanced', 'grayscale', 'otsu' ya 'adaptive')"
    )
