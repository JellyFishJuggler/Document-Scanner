"""
scanner.py
Public API facade. Asli implementation `components/` package me hai; yahan sab
re-export hota hai taaki `from scanner import ...` waise hi chalta rahe.

Koi camera/UI/file-write dependency nahi - poora kaam memory me hota hai.
main.py (local tool) aur api.py (HTTP server) ise use karte hain.
"""

from components.geometry import (
    order_corners,
    four_point_transform,
    resize_to_fit,
    scale_corners,
)
from components.detection import scan_detection
from components.corners import validate_corners
from components.page import prepare_page
from components.binarize import binarize, scan_image, binarize_document
from components.enhancement import (
    _odd,
    estimate_illumination,
    estimate_background,
    remove_shadow,
    enhance,
    normalize_illumination,
    local_contrast,
    reduce_noise,
    sharpen,
    enhance_document,
)
from components.render import render_scan

__all__ = [
    "order_corners",
    "four_point_transform",
    "resize_to_fit",
    "scale_corners",
    "scan_detection",
    "validate_corners",
    "prepare_page",
    "binarize",
    "scan_image",
    "binarize_document",
    "estimate_illumination",
    "estimate_background",
    "remove_shadow",
    "enhance",
    "normalize_illumination",
    "local_contrast",
    "reduce_noise",
    "sharpen",
    "enhance_document",
    "render_scan",
]
