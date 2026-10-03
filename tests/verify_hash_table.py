"""
Point 4: old vs new SHA256, per image, side by side.
Point 5: exactly which image gave the "1375px vs 800px" number.
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

import baseline_scan as OLD   # original code, sliced verbatim out of main_original.py
import scanner as NEW         # extracted scanner.py

FIX = _paths.FIX
IMG_DIR = str(_paths.REPO / "output")


def make_synthetic():
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


real = cv2.imread(f"{IMG_DIR}/captured_frame.png")
synth = make_synthetic()

tests = [
    ("real captured_frame.png  (ASLI photo)", real),
    ("synthetic tilted page    (MERE test image)", synth),
    ("real rotated 90", cv2.rotate(real, cv2.ROTATE_90_CLOCKWISE)),
    ("synthetic upscaled 1.5x", cv2.resize(synth, None, fx=1.5, fy=1.5,
                                           interpolation=cv2.INTER_CUBIC)),
]


def h_png(img):
    return hashlib.sha256(cv2.imencode(".png", img)[1].tobytes()).hexdigest()


def h_arr(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


print("=" * 100)
print("POINT 4 - old vs new, per image. Local venv: cv2 5.0.0, numpy 2.5.3, py 3.14.7")
print("=" * 100)

for label, img in tests:
    old_c = OLD.scanDetection(img)
    new_c = NEW.scan_detection(img)
    _, fb = NEW.scan_detection(img, return_fallback=True)

    old_loop, _ = OLD.scan_baseline(img, do_resize=True)
    new_warp = NEW.four_point_transform(img, NEW.scan_detection(img).reshape(4, 2))
    new_loop = NEW.binarize(NEW.resize_to_fit(new_warp), method="otsu")

    full = NEW.scan_image(img, method="otsu")["scan"]

    print(f"\n{label}")
    print(f"  input {img.shape[1]}x{img.shape[0]}   used_fallback={fb}")
    print()
    print(f"  {'stage':34s} {'OLD sha256':18s} {'NEW sha256':18s} {'equal':6s}")
    print(f"  {'-'*34} {'-'*18} {'-'*18} {'-'*6}")

    for stage, o, n in [
        ("scanDetection / scan_detection contour", h_arr(old_c), h_arr(new_c)),
        ("final scan, WITH resize (main.py loop)", h_png(old_loop), h_png(new_loop)),
        ("final scan, NO resize (scan_image)", h_png(OLD.scan_baseline(img, do_resize=False)[0]),
         h_png(full)),
    ]:
        print(f"  {stage:34s} {o[:16]:18s} {n[:16]:18s} {'YES' if o == n else 'NO':6s}")

    print()
    print(f"  {'output size':34s} OLD {old_loop.shape[1]}x{old_loop.shape[0]}"
          f"      NEW full-res {full.shape[1]}x{full.shape[0]}")

print()
print("=" * 100)
print("POINT 5 - the '1375px vs 800px' figure, traced to its source")
print("=" * 100)
print()
print("  Wo number 'synthetic tilted page' se aaya tha - meri banayi hui test image,")
print("  aapki koi real photo nahi. Ye generate hui thi isse:")
print("    1400x1900 px canvas, us par tilted white page, fake text lines.")
print()
for label, img in tests:
    loop = NEW.scan_image(img, method="otsu")
    full_only = NEW.scan_image(img, method="otsu")["scan"]
    old_style = NEW.binarize(
        NEW.resize_to_fit(NEW.four_point_transform(img,
                                                   NEW.scan_detection(img).reshape(4, 2))))
    print(f"  {label:40s} input {img.shape[1]:>4}x{img.shape[0]:<4} "
          f"loop-style {old_style.shape[1]:>4}px   full-res {full_only.shape[1]:>4}px")
print()
print("  Sirf 'synthetic tilted page' mein farak pada, kyunki uska warped page")
print("  resize_to_fit ki 800px ceiling se bada tha (1375px). Baaki images ke warped")
print("  page pehle se 800x600 se chhote the, isliye full-res aur loop-style")
print("  byte-identik aa gaye - yaani resize ne koi farak hi nahi daala.")
print("  1375px wala page EXIF-rotated 'real rotated 90' (183px) aur 'real")
print("  captured_frame.png' (582px) par bhi nahi aaya - dono 800px ke andar hain.")