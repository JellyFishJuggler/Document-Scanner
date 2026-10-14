"""
corners.py - client ke bheje corners ki validation.
"""
import cv2
import numpy as np

from .geometry import order_corners


def validate_corners(corners, tolerance=0.02):
    """Client corners check karo; invalid par ValueError (API 422 deti hai).

    Rules: 4 points, normalized 0..1 (thodi tolerance ke saath), convex, aur
    area >= 5% frame. Return: TL/TR/BR/BL order me float32 corners.
    """
    try:
        pts = np.asarray(corners, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"corners ko numbers ki list chahiye: {exc}") from exc

    if pts.shape != (4, 2):
        raise ValueError(f"corners me exactly 4 [x, y] points hone chahiye, mila shape {pts.shape}")

    if not np.isfinite(pts).all():
        raise ValueError("corners me NaN ya Infinity nahi ho sakta")

    if pts.min() < -tolerance or pts.max() > 1 + tolerance:
        raise ValueError(
            f"corners normalized 0..1 ke andar hone chahiye, mila "
            f"min={pts.min():.3f} max={pts.max():.3f}"
        )

    pts = np.clip(pts, 0.0, 1.0)
    ordered = np.array(order_corners(pts), dtype=np.float32)

    # Area canonical order me ginna: cv2.contourArea order-sensitive hai, warna
    # valid bowtie quad ka area ~0 aake galat reject ho jaata.
    area = cv2.contourArea(ordered)
    if area < 0.05:
        raise ValueError(f"corners ka area {area:.1%} hai, kam se kam 5% chahiye")

    # float32 me check (int32 me sab 0/1 ho jaate hain); shape (4, 1, 2) chahiye.
    if not cv2.isContourConvex(ordered.reshape(-1, 1, 2)):
        raise ValueError("corners convex quadrilateral nahi hain (ek corner bahar ya andar hai)")

    return ordered
