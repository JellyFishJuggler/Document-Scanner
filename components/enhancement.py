"""
enhancement.py - lighting/shadow correction aur scan enhancement helpers.
"""
import cv2
import numpy as np


def _odd(value):
    """Kernel/block size ko odd rakho (OpenCV even par center theek nahi leta)."""
    return int(value) | 1


def estimate_illumination(gray, work_max=None, kernel_ratio=12, blur_sigma=0.0):
    """Page ka slow-varying background/illumination map.

    Bade kernel ka morphological CLOSE text/lines ko hata deta hai; jo bacha
    wahi paper illumination hai. `work_max` par chhoti copy par estimate karke
    wapas upscale karte hain (roshni smooth hoti hai, isliye fast + same).
    """
    h, w = gray.shape[:2]
    scale = 1.0
    work = gray
    if work_max and max(h, w) > work_max:
        scale = work_max / max(h, w)
        work = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    k = _odd(max(15, min(work.shape[:2]) // kernel_ratio))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(work, cv2.MORPH_CLOSE, kernel)

    if blur_sigma > 0:
        bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=blur_sigma)

    if scale != 1.0:
        bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return bg


def estimate_background(img):
    """`estimate_illumination` ka full-resolution wrapper (purana behaviour, parity)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return estimate_illumination(gray)


def remove_shadow(img):
    """Uneven lighting hatao: har pixel ko local background se divide karo."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = estimate_background(img)
    # float me divide karo - uint8 zero-division silent zero deta hai.
    bg_f = bg.astype(np.float32) + 1.0
    flat = np.clip(gray.astype(np.float32) * 255.0 / bg_f, 0, 255).astype(np.uint8)
    return cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)


def enhance(img, preset="original", shadow_removal=False):
    """Filter presets: original, grayscale, bw, bw_adaptive, clean_white.

    Return dict: image, channels (1 ya 3), timing_ms (shadow + preset).
    """
    import time as _time

    t0 = _time.perf_counter()
    work = remove_shadow(img) if shadow_removal else img
    t1 = _time.perf_counter()

    if preset == "original":
        out = work
    elif preset == "grayscale":
        out = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    elif preset == "bw":
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif preset == "bw_adaptive":
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        out = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )
    elif preset == "clean_white":
        # Apna lighting flat karta hai; shadow_removal=True ho to already flat.
        flat = work if shadow_removal else remove_shadow(img)
        gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
        lo, hi = np.percentile(gray, (2, 98))
        if hi - lo < 1:  # khaali/flat page - stretch mat karo
            out = gray
        else:
            stretched = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
            out = np.clip((stretched.astype(np.float32) - 8) * 1.06, 0, 255).astype(np.uint8)
    else:
        raise ValueError(
            f"unknown preset: {preset!r} (use 'original', 'grayscale', 'bw', "
            f"'bw_adaptive' ya 'clean_white')"
        )
    t2 = _time.perf_counter()

    channels = 1 if out.ndim == 2 else out.shape[2]
    return {
        "image": out,
        "channels": channels,
        "timing_ms": {"shadow": (t1 - t0) * 1000, "preset": (t2 - t1) * 1000},
    }


def normalize_illumination(img, target=250.0, max_gain=3.5, work_max=512):
    """Flat-field shadow removal: har pixel ko local background se divide karo.

    Teeno channels par same gain (grayscale background se) - colour shift nahi.
    `kernel_ratio=6` chhota rakha hai taaki bade dark content (logo/header) ka
    gain ~1 rahe. `relative_dark` mask ink/dark pixels ka gain 1 ki taraf kheenchta
    hai, par shadow wale paper (gray ~= bg) par full correction lagti hai.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = estimate_illumination(
        gray, work_max=work_max, kernel_ratio=6, blur_sigma=4.0
    ).astype(np.float32) + 1.0
    gain = np.clip(target / bg, 1.0, max_gain)
    relative_dark = np.clip((bg - gray.astype(np.float32)) / 64.0, 0.0, 1.0)
    gain = 1.0 + (gain - 1.0) * (1.0 - relative_dark)
    b, g, r = cv2.split(img)
    return cv2.merge([
        cv2.multiply(b, gain, dtype=cv2.CV_8U),
        cv2.multiply(g, gain, dtype=cv2.CV_8U),
        cv2.multiply(r, gain, dtype=cv2.CV_8U),
    ])


def local_contrast(img, clip_limit=1.4):
    """Halka CLAHE sirf luminance (LAB L) par - colour safe."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def reduce_noise(img, diameter=5, strength=24):
    """Edge-preserving bilateral denoise (text edges blur nahi hote)."""
    return cv2.bilateralFilter(img, diameter, strength, strength)


def sharpen(img, amount=0.3, sigma=1.0):
    """Moderate unsharp mask (zyada amount se text par halo banta hai)."""
    blurred = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blurred, -amount, 0)


def enhance_document(img):
    """Colour clean scan: normalize -> local contrast -> denoise -> sharpen."""
    flat = normalize_illumination(img)
    contrasted = local_contrast(flat)
    smoothed = reduce_noise(contrasted)
    return sharpen(smoothed)
