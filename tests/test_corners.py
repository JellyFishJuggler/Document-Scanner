"""Phase B - /scan corners override.

Spec ke 8 corners requirements ke saare test, HTTP level par (real container).

Run:  .venv/bin/python tests/test_corners.py
"""
import base64
import json
import sys
import urllib.error
import urllib.request

import cv2
import numpy as np

import _paths
from _paths import API, ARTIFACTS, REPO

sys.path.insert(0, str(REPO))
from scanner import validate_corners  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def post(path, payload):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:200].decode("utf-8", "replace")}


def unsupported_b64():
    """A valid PNG header, then junk - enough to trip the format allow-list."""
    from _paths import FIX
    p = FIX / "fake.tif"
    p.write_bytes(b"II\x2a\x00" + bytes(range(200)))
    return base64.b64encode(p.read_bytes()).decode()


def marker_fraction(img):
    """Fraction of pixels that are the cyan marker (filled BGR 40,235,240).

    Blue is the LOW channel here - getting the order wrong silently reads 0%.
    """
    b, g, r = cv2.split(img)
    return float(((b < 80) & (g > 200) & (r > 200)).mean())


def b64(img, fmt=".png"):
    ok, buf = cv2.imencode(fmt, img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


# A page in a frame, so automatic detection has a real document to find.
def build_photo(w=1600, h=1200):
    img = np.full((h, w, 3), 60, np.uint8)
    cv2.rectangle(img, (0, 0), (w, 90), (40, 45, 55), -1)
    for i in range(8):  # window light so the desk is not perfectly uniform
        cv2.rectangle(img, (60 + i * 200, 100), (150 + i * 200, 420), (90, 80, 70), -1)
    page = np.array([[240, 200], [1360, 230], [1330, 1030], [270, 990]], np.int32)
    cv2.fillConvexPoly(img, page, (243, 242, 239))
    cv2.rectangle(img, (300, 300), (1250, 360), (35, 35, 40), -1)
    for i in range(22):
        cv2.rectangle(img, (300, 420 + i * 26), (1180, 436 + i * 26), (70, 70, 76), -1)
    return img, page


def norm_of(page, w, h):
    return [[float(x) / w, float(y) / h] for x, y in page.tolist()]


print("=" * 74)
print("PHASE B - /scan corners override")
print("=" * 74)

photo, page = build_photo()
W, H = photo.shape[1], photo.shape[0]
img_b64 = b64(photo)

print()
print("1. corners override produces the expected warp")

# A bright marker on a DARK background. Cropping exactly the marker quad must
# return an image that is entirely marker - if the server ignored our corners
# and auto-detected instead, the dark background would show up and this fails.
# This is a geometric assertion, not a "looks about right" one.
dark = np.full((H, W, 3), 20, np.uint8)
quad = np.array([[0.30, 0.25], [0.70, 0.25], [0.70, 0.75], [0.30, 0.75]], np.float32)
quad_px = (quad * [W, H]).astype(np.int32)
cv2.fillConvexPoly(dark, quad_px, (40, 235, 240))
marker_b64 = b64(dark)

st, r = post("/scan", {"image": marker_b64, "corners": quad.tolist(), "max_side": None})
check("status 200", st == 200, f"got {st} {r.get('raw', '')[:90]}")
if st == 200:
    got = np.array(r["corners"], np.float32)
    check("corners echoed back as normalized, unchanged",
          np.allclose(got, quad, atol=1e-3), f"got {got.tolist()}")
    # float32 round-trip, isliye exact == nahi - atol ke saath compare karo.
    expect_ordered = np.array([[0.3, 0.25], [0.7, 0.25], [0.7, 0.75], [0.3, 0.75]], np.float32)
    check("corners are ordered TL/TR/BR/BL",
          np.allclose(got, expect_ordered, atol=1e-4),
          f"{got.tolist()} want {expect_ordered.tolist()}")
    check("corners_source == client", r["corners_source"] == "client")
    check("detected is true when corners supplied", r["detected"] is True)
    check("used_fallback is false", r["used_fallback"] is False)

    ww, wh = r["warped_size"]
    check("warped_size == the requested quad",
          abs(ww - 0.40 * W) <= 2 and abs(wh - 0.50 * H) <= 2,
          f"got {ww}x{wh}, want ~{int(0.40 * W)}x{int(0.50 * H)}")

    warped = cv2.imdecode(
        np.frombuffer(base64.b64decode(r["warped_b64"]), np.uint8), cv2.IMREAD_COLOR)
    check("warped_b64 decodes at the advertised size",
          warped is not None and warped.shape[:2] == (wh, ww),
          f"{None if warped is None else warped.shape}")
    if warped is not None:
        # marker was filled BGR (40, 235, 240) -> blue channel LOW, G and R high.
        cyan = marker_fraction(warped)
        check("warped image is the MARKER quad (not the dark background)",
              cyan > 0.95, f"{cyan:.1%} marker pixels")

    # A different quad on the same image must give a different result.
    other = np.array([[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]], np.float32)
    st2, r2 = post("/scan", {"image": marker_b64, "corners": other.tolist(), "max_side": None})
    if st2 == 200:
        w2 = cv2.imdecode(
            np.frombuffer(base64.b64decode(r2["warped_b64"]), np.uint8), cv2.IMREAD_COLOR)
        cyan2 = marker_fraction(w2)
        check("a DIFFERENT quad gives a DIFFERENT warp (corners really used)",
              abs(r2["warped_size"][0] - ww) > 50 and cyan2 < 0.5,
              f"marker {cyan2:.1%}, size {ww}x{wh} -> {r2['warped_size']}")
    else:
        check("different quad accepted", False, f"status {st2}")

    for f in ("scan_b64", "used_fallback", "detected", "contour", "source_size",
              "scan_size", "method", "timing_ms", "contour",
              "corners", "warped_b64", "warped_size", "warped_mime", "corners_source"):
        check(f"response field '{f}' present", f in r)

# On the photo, the requested crop is inside the page so the B/W scan has text.
want = np.array([[0.25, 0.30], [0.75, 0.30], [0.75, 0.70], [0.25, 0.70]], np.float32)
st, rp = post("/scan", {"image": img_b64, "corners": want.tolist(), "max_side": None})
check("photo + corners -> 200", st == 200, f"got {st}")
if st == 200:
    scan = cv2.imdecode(
        np.frombuffer(base64.b64decode(rp["scan_b64"]), np.uint8), cv2.IMREAD_GRAYSCALE)
    vals = set(np.unique(scan).tolist())
    check("scan_b64 is strictly B&W (values 0 and 255 only)", vals <= {0, 255},
          f"{len(vals)} distinct values")
    check("crop really contains the ruled text (not blank desk)",
          (scan < 128).mean() > 0.05, f"{(scan < 128).mean():.1%} ink")

print()
print("   1b. auto detection vs client corners on the photo")
st_a, r_det = post("/scan", {"image": img_b64, "max_side": None})
st_b, r_cor = post("/scan", {"image": img_b64, "corners": want.tolist(), "max_side": None})
if st_a == 200 and st_b == 200:
    check("corners_source differs",
          r_det["corners_source"] == "detected" and r_cor["corners_source"] == "client")
    check("warped_size differs (supplied is the 50%x40% rect)",
          r_det["warped_size"] != r_cor["warped_size"],
          f"auto={r_det['warped_size']} supplied={r_cor['warped_size']}")
    check("auto detection found the page (not fallback)", r_det["used_fallback"] is False)

print()
print("   1c. corners accepted in ANY order")
import itertools
ref = None
for perm in itertools.permutations(range(4)):
    st, rr = post("/scan", {"image": img_b64, "corners": [want[i].tolist() for i in perm],
                            "max_side": None})
    if st != 200:
        check(f"permutation {perm} accepted", False, f"status {st}")
        break
    g = np.array(rr["corners"], np.float32)
    if ref is None:
        ref = g
    if not np.allclose(g, ref, atol=1e-3):
        check(f"permutation {perm} -> same ordered corners", False, f"{g.tolist()}")
        break
else:
    check(f"all 24 permutations give identical TL/TR/BR/BL corners", True)

print()
print("2. invalid corners -> 422")
bad = {
    "only 3 points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]],
    "5 points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.5, 0.5], [0.2, 0.9]],
    "negative coord": [[-0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
    "coord > 1": [[0.1, 0.1], [1.4, 0.1], [0.9, 0.9], [0.1, 0.9]],
    "far out of range": [[0, 0], [3, 0], [3, 3], [0, 3]],
    "area 0.16% (<5%)": [[0.4, 0.4], [0.44, 0.4], [0.44, 0.44], [0.4, 0.44]],
    "concave quad": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.5], [0.1, 0.9]],
    "wrong shape (4 scalars)": [0.1, 0.2, 0.8, 0.9],
    "wrong shape (4x3)": [[0.1, 0.1, 0], [0.9, 0.1, 0], [0.9, 0.9, 0], [0.1, 0.9, 0]],
    "not numbers": ["a", "b", "c", "d"],
    "null point": [[0.1, 0.1], [0.9, 0.1], None, [0.1, 0.9]],
    "degenerate collinear": [[0.1, 0.5], [0.5, 0.5], [0.9, 0.5], [0.5, 0.5]],
}
for name, c in bad.items():
    st, rr = post("/scan", {"image": img_b64, "corners": c})
    check(f"422  {name}", st == 422, f"got {st}")

print()
print("   2b. corners: null means 'detect automatically', not an error")
st, rr = post("/scan", {"image": img_b64, "corners": None})
check("corners:null -> 200 and auto detection", st == 200 and rr["corners_source"] == "detected",
      f"status {st}")

print()
print("   2c. corners do NOT weaken the existing guardrails")
fix = ARTIFACTS / "fixtures"

# Corners must not bypass ANY Phase A guardrail: the pixel bomb, the corrupt
# file, auth and the real progressive JPEG all have to behave exactly as before.
for name, data, want_st in (
    ("413 pixel bomb",
     base64.b64encode((fix / "huge.jpg").read_bytes()).decode(), 413),
    ("400 corrupt",
     base64.b64encode((fix / "corrupt.jpg").read_bytes()).decode(), 400),
    ("415 unsupported", unsupported_b64(), 415),
):
    st, _ = post("/scan", {"image": data, "corners": want.tolist()})
    check(f"{name} still enforced with corners supplied", st == want_st, f"got {st}")

st, _ = post("/scan", {
    "image": base64.b64encode((fix / "progressive.jpg").read_bytes()).decode(),
    "corners": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
})
check("real progressive JPEG + corners -> 200", st == 200, f"got {st}")

st, _ = post("/scan", {"image": img_b64, "corners": want.tolist()})
check("corner validation still applies without any auth layer", st == 200, f"got {st}")

st, rr = post("/scan", {"image": img_b64, "corners": want.tolist(), "max_side": 99999})
check("422 max_side range still enforced", st == 422, f"got {st}")

print()
print("   2d. phone_photo.jpg + corners (4000x3000, EXIF orientation 6)")
phone = REPO / "test_fixtures" / "phone_photo.jpg"
if phone.exists():
    st, rr = post("/scan", {
        "image": base64.b64encode(phone.read_bytes()).decode(),
        "corners": [[0.12, 0.11], [0.90, 0.15], [0.87, 0.90], [0.14, 0.87]],
        "max_side": 2000,
    })
    check("phone_photo + corners -> 200", st == 200, f"got {st}")
    if st == 200:
        check("corners_source client", rr["corners_source"] == "client")
        check("source_size is the decoded (EXIF-rotated) shape 3000x4000",
              rr["source_size"] == [3000, 4000], str(rr["source_size"]))
        check("warped_size sane", rr["warped_size"][0] > 100 and rr["warped_size"][1] > 100,
              str(rr["warped_size"]))
        print(f"        timing_ms = {rr['timing_ms']}")
else:
    print("  SKIP  test_fixtures/phone_photo.jpg missing - run make_phone_fixture.py")

print()
print("=" * 74)
failed = [n for n, ok in RESULTS if not ok]
print(f"TOTAL: {len(RESULTS)}   PASS {len(RESULTS) - len(failed)}   FAIL {len(failed)}")
if failed:
    print("FAILURES:")
    for n in failed:
        print(f"  - {n}")
sys.exit(1 if failed else 0)