"""
Phase 1 verification: old code vs new code, same venv, byte-for-byte.

Runs in the project venv (same cv2/numpy as your local tool).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths
import hashlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_paths.REPO))

import cv2
import numpy as np

import baseline_scan as OLD
import scanner as NEW

print("=" * 62)
print("local venv versions")
print("=" * 62)
print("  cv2.__version__   :", cv2.__version__)
print("  numpy.__version__ :", np.__version__)
print("  python            :", sys.version.split()[0])
print()

IMG_DIR = str(_paths.REPO / "output")


def png_hash(img):
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return hashlib.sha256(buf.tobytes()).hexdigest()


def make_synthetic():
    """white page, tilted, on a dark desk, with fake text lines"""
    img = np.full((1400, 1900, 3), 45, np.uint8)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    page = np.array([[260, 180], [1640, 300], [1560, 1230], [330, 1150]], np.int32)
    cv2.fillConvexPoly(img, page, (238, 238, 240))
    cv2.polylines(img, [page], True, (120, 120, 120), 4)
    for i in range(22):
        y = 380 + i * 36
        w = 900 - (i % 4) * 130
        cv2.line(img, (380, y), (380 + w, y + 18), (55, 55, 60), 3)
    cv2.line(img, (380, 300), (1180, 300), (30, 30, 35), 5)
    return img


tests = {}

real = cv2.imread(f"{IMG_DIR}/captured_frame.png")
tests["real captured_frame.png"] = real

synth = make_synthetic()
tests["synthetic tilted page"] = synth

tests["real rotated 90"] = cv2.rotate(real, cv2.ROTATE_90_CLOCKWISE)
tests["synthetic upscaled"] = cv2.resize(synth, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)

fails = 0

for name, img in tests.items():
    print("=" * 62)
    print(name, "|", img.shape)
    print("=" * 62)

    # ---- A: contour parity (pure extraction claim) ----
    c_old = OLD.scanDetection(img)
    c_new = NEW.scan_detection(img)
    a_ok = np.array_equal(c_old, c_new)
    _, fb = NEW.scan_detection(img, return_fallback=True)
    print(f"  A contour parity            : {'PASS' if a_ok else 'FAIL'}")
    print(f"    used_fallback={fb}   contour dtype={c_new.dtype} shape={c_new.shape}")
    fails += (not a_ok)

    # return_fallback default must stay OFF (main.py depends on it)
    d_ok = isinstance(NEW.scan_detection(img), np.ndarray)
    print(f"    return_fallback default off: {'PASS' if d_ok else 'FAIL'}")
    fails += (not d_ok)

    # ---- B: full pipeline WITH resize == main.py's loop ----
    s_old, _ = OLD.scan_baseline(img, do_resize=True)
    w = NEW.four_point_transform(img, NEW.scan_detection(img).reshape(4, 2))
    s_new = NEW.binarize(NEW.resize_to_fit(w), method="otsu")
    h_old, h_new = png_hash(s_old), png_hash(s_new)
    b_ok = h_old == h_new and s_old.shape == s_new.shape
    print(f"  B main.py loop parity       : {'PASS' if b_ok else 'FAIL'}")
    print(f"    old sha256 {h_old}")
    print(f"    new sha256 {h_new}")
    print(f"    size  old={s_old.shape} new={s_new.shape}")
    fails += (not b_ok)

    # ---- C: full pipeline WITHOUT resize == scan_image(max_side=None) ----
    s_old2, _ = OLD.scan_baseline(img, do_resize=False)
    res = NEW.scan_image(img, method="otsu")
    h_old2, h_new2 = png_hash(s_old2), png_hash(res["scan"])
    c_ok = h_old2 == h_new2 and s_old2.shape == res["scan"].shape
    print(f"  C scan_image parity (no resize): {'PASS' if c_ok else 'FAIL'}")
    print(f"    old sha256 {h_old2}")
    print(f"    new sha256 {h_new2}")
    print(f"    size  old={s_old2.shape} new={res['scan'].shape}")
    fails += (not c_ok)

    # ---- D: the intentional difference (OCR argument) ----
    full = NEW.scan_image(img)["scan"].shape
    loop = s_old.shape
    print(f"  D full-res vs loop-resize  : full={full} loop={loop}  (expected to differ)")
    print(f"    full-res keeps {full[1]}px wide vs loop {loop[1]}px")
    print()

# ---- E: adaptive opt-in ----
print("=" * 62)
print("E adaptive method (opt-in)")
print("=" * 62)
o = NEW.scan_image(synth, method="otsu")["scan"]
a1 = NEW.scan_image(synth, method="adaptive")["scan"]
a2 = NEW.scan_image(synth, method="adaptive")["scan"]
print("  otsu sha256                :", png_hash(o))
print("  adaptive sha256            :", png_hash(a1))
print("  adaptive deterministic     :", "PASS" if png_hash(a1) == png_hash(a2) else "FAIL")
print("  adaptive differs from otsu :", "PASS" if png_hash(a1) != png_hash(o) else "FAIL")
print("  shapes match               :", "PASS" if a1.shape == o.shape else "FAIL")
try:
    NEW.binarize(synth, method="bogus")
    print("  bad method raises          : FAIL")
except ValueError as e:
    print("  bad method raises          : PASS", f"({e})")

# ---- F: max_side bounds input only ----
print()
print("=" * 62)
print("F max_side")
print("=" * 62)
big = cv2.resize(synth, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
print("  input                      :", big.shape)
r = NEW.scan_image(big, max_side=1200)
print("  after max_side=1200        :", r["scan"].shape)
bounded = max(r["scan"].shape[:2]) > 0
print("  produced output            :", "PASS" if bounded else "FAIL")

print()
print("=" * 62)
print("TOTAL FAILURES:", fails)
print("=" * 62)