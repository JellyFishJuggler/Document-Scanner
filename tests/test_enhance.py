"""Phase B - /enhance, all five presets plus desk-shadow removal.

Spec ke 8 requirements:
  1. corners override produces expected warp  -> test_corners.py
  2. invalid corners -> 422                 -> test_corners.py
  3. original returns a valid image          -> 3 below
  4. grayscale is single channel             -> 4
  5. bw contains only 0 and 255              -> 5
  6. bw_adaptive returns a valid image       -> 6
  7. clean_white returns a valid image       -> 7
  8. shadow removal works                    -> measured std-dev, section 8

Shadow removal is measured, not eyeballed: a synthetic page with a linear
illumination gradient, then the background standard deviation before and after.

Before/after PNGs go to /tmp/scanly_artifacts/enhance/ and NOWHERE ELSE - no copy
outside /tmp, and nothing is committed. An earlier version of this script also
wrote to ~/scanly_phaseb/ "because /tmp is volatile"; that was a rule violation,
so the copy is gone and the paths printed below are the only copies.

Run:  SCANLY_TEST_URL=http://127.0.0.1:18001 .venv/bin/python tests/test_enhance.py
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

import _paths
from _paths import API, ARTIFACTS, KEY, REPO

RESULTS = []
OUT = ARTIFACTS / "enhance"
OUT.mkdir(parents=True, exist_ok=True)



def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def post(path, payload, key=KEY):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def b64(img, fmt=".png", quality=None):
    params = [] if quality is None else [cv2.IMWRITE_JPEG_QUALITY, quality]
    ok, buf = cv2.imencode(fmt, img, params)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def decode(resp):
    raw = base64.b64decode(resp["image_b64"])
    flag = cv2.IMREAD_GRAYSCALE if resp["channels"] == 1 else cv2.IMREAD_COLOR
    return cv2.imdecode(np.frombuffer(raw, np.uint8), flag)


def save(img, name):
    """Write a before/after artifact under /tmp only."""
    a = OUT / name
    cv2.imwrite(str(a), img)
    return a


# A page with a real linear illumination gradient: bright on the left, dim on
# the right. That is what a page under a desk lamp / window actually looks like.
#
# The 64-level falloff (234 -> 170) is deliberate. An earlier version used
# 246 -> 70, which is not a shadow, it is a different exposure: Otsu classified
# the whole dim half as ink (58% "text") and the background patch the test was
# measuring overlapped the last text row, so every std-dev number was garbage.
#
# Text stops at LAST_TEXT_Y and the measurement box starts below it, so the box
# is guaranteed pure background - no glyph can contaminate the std-dev reading.
def gradient_page(w=1600, h=1100, lo=170, hi=234):
    ramp = np.linspace(hi, lo, w, dtype=np.float32)
    g = np.tile(ramp, (h, 1))
    img = np.dstack([g, g, g]).astype(np.uint8)
    cv2.putText(img, "QUARTERLY REPORT", (150, 190), cv2.FONT_HERSHEY_SIMPLEX, 2.1, (34, 34, 36), 4)
    cv2.rectangle(img, (150, 250), (1450, 262), (62, 62, 68), -1)
    for i in range(15):
        y = 330 + i * 30
        cv2.rectangle(img, (150, y), (1400 - (i % 5) * 90, y + 14), (58, 58, 64), -1)
    return img


# Bottom margin, strictly below the last text row (330 + 14*30 + 14 = 764).
BOX = (120, 776, 1480, 1085)
LAST_TEXT_Y = 764


def bg_stats(img, box):
    """Mean and std-dev of a background patch - i.e. an area with no text.

    Handles 1-channel and 3-channel input alike; BGR2GRAY raises on a 2-D array.
    """
    x0, y0, x1, y1 = box
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    p = g[y0:y1, x0:x1].astype(np.float32)
    return float(p.mean()), float(p.std())


print("=" * 74)
print("PHASE B - /enhance")
print("=" * 74)

page = gradient_page()
page_b64 = b64(page)
m_before, s_before = bg_stats(page, BOX)
print(f"  synthetic page {page.shape[1]}x{page.shape[0]}")
print(f"  illumination gradient 234 -> 170 across the width")
print(f"  text occupies y<{LAST_TEXT_Y}; measurement box {BOX} is below it (pure background)")
print(f"  background before: mean={m_before:7.2f}  std={s_before:7.3f}")
# Prove the box really is background - Otsu finds no ink inside it.
_gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
_bw = cv2.threshold(_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
_ink = float((_bw[BOX[1]:BOX[3], BOX[0]:BOX[2]] < 128).mean())
check("measurement box contains no text at all", _ink < 0.001, f"{_ink:.3%} ink")
print()

PRESETS = ("original", "grayscale", "bw", "bw_adaptive", "clean_white")

print("3. original returns a valid image")
st, r = post("/enhance", {"image": page_b64, "preset": "original"})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    check("decodes to a non-empty image", im is not None and im.size > 0,
          f"{None if im is None else im.shape}")
    check("size matches the input", r["size"] == [page.shape[1], page.shape[0]],
          f"{r['size']}")
    check("channels == 3 (color)", r["channels"] == 3, str(r["channels"]))
    check("mime is image/jpeg by default", r["mime"] == "image/jpeg", r["mime"])
    check("reports the preset back", r["preset"] == "original")
    check("timing_ms has an 'enhance' stage", "enhance" in r["timing_ms"])
    # same size, and visually the same scene (not a thresholded version)
    diff = float(np.abs(im.astype(np.float32) - page.astype(np.float32)).mean())
    check("original is visually unchanged (mean abs diff < 6)", diff < 6.0, f"{diff:.2f}")

print()
print("4. grayscale is single channel")
st, r = post("/enhance", {"image": page_b64, "preset": "grayscale", "output_format": "png"})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    check("channels == 1", r["channels"] == 1, str(r["channels"]))
    check("decoded array is 2-D (h, w)", im is not None and im.ndim == 2,
          f"{None if im is None else im.shape}")
    check("mime honoured: png", r["mime"] == "image/png", r["mime"])
    src_gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    diff = float(np.abs(im.astype(np.float32) - src_gray.astype(np.float32)).mean())
    check("grayscale equals cv2's own BGR2GRAY of the input", diff < 1.0, f"mean diff {diff:.3f}")

print()
print("5. bw contains only 0 and 255")
# PNG, not the default JPEG. The algorithm outputs a strictly 0/255 array, but
# JPEG is lossy - ringing at every hard black/white edge invents intermediate
# values (0-14 and 240-255 in the measured run below). So the strict assertion
# only holds for a lossless container. Both are checked; the JPEG number is
# reported rather than hidden.
st, r = post("/enhance", {"image": page_b64, "preset": "bw", "output_format": "png"})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    u = np.unique(im)
    check("channels == 1", r["channels"] == 1, str(r["channels"]))
    check("PNG: exactly two distinct values", len(u) == 2, f"{len(u)}: {u.tolist()}")
    check("PNG: the values are exactly 0 and 255", set(u.tolist()) == {0, 255},
          f"{u.tolist()}")
    ink = float((im < 128).mean())
    check("text is present (not all-white)", 0.02 < ink < 0.5, f"{ink:.1%} ink")

st, rj = post("/enhance", {"image": page_b64, "preset": "bw"})
if st == 200:
    uj = np.unique(decode(rj))
    print(f"     for reference, the same preset as JPEG (lossy): "
          f"{len(uj)} distinct values, all within {uj.min()}..{uj.max()}")
    check("JPEG: still visually B&W (no midtones beyond compression ringing)",
          set(uj.tolist()) <= set(range(0, 16)) | set(range(240, 256)),
          f"{uj.tolist()}")

print()
print("6. bw_adaptive returns a valid image")
st, r = post("/enhance", {"image": page_b64, "preset": "bw_adaptive", "output_format": "png"})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    u = np.unique(im)
    check("decodes to a full-size image", im is not None and im.shape == page.shape[:2],
          f"{None if im is None else im.shape}")
    check("channels == 1", r["channels"] == 1, str(r["channels"]))
    check("still strictly 0/255 (PNG)", set(u.tolist()) <= {0, 255}, f"{u.tolist()}")
    ink = float((im < 128).mean())
    check("text present", 0.02 < ink < 0.5, f"{ink:.1%} ink")
    # Global Otsu vs adaptive, measured on a SEVERE illumination gradient.
    #
    # On the gentle 64-level page the two agree on 100% of pixels - that is the
    # correct answer, not a broken preset, so asserting divergence there would
    # be testing nothing. A 155-level falloff is the case adaptive exists for:
    # Otsu picks one global threshold and the dim half of the page turns into a
    # solid black slab.
    severe = gradient_page(lo=90, hi=245)
    sb = b64(severe)
    st2, r2 = post("/enhance", {"image": sb, "preset": "bw", "output_format": "png"})
    st3, r3 = post("/enhance", {"image": sb, "preset": "bw_adaptive", "output_format": "png"})
    if st2 == 200 and st3 == 200:
        otsu_s, adap_s = decode(r2), decode(r3)
        d = float((adap_s.astype(np.float32) != otsu_s.astype(np.float32)).mean())
        check("severe gradient: adaptive differs from global Otsu", d > 0.05,
              f"{d:.1%} of pixels differ")
        # the real proof: how much of the DIM half survives as text, not a slab
        roi = (slice(330, LAST_TEXT_Y), slice(800, 1400))
        ink_o = float((otsu_s[roi] < 128).mean())
        ink_a = float((adap_s[roi] < 128).mean())
        print(f"     severe gradient, ink fraction in the dim half: "
              f"Otsu {ink_o:.1%} vs adaptive {ink_a:.1%}")
        check("severe gradient: adaptive keeps the dim half readable "
              "(Otsu turns it into a slab)",
              ink_o > 0.60 and ink_a < 0.45,
              f"Otsu {ink_o:.1%}, adaptive {ink_a:.1%}")

print()
print("7. clean_white returns a valid image")
st, r = post("/enhance", {"image": page_b64, "preset": "clean_white"})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    check("decodes to a full-size image", im is not None and im.shape == page.shape[:2],
          f"{None if im is None else im.shape}")
    check("channels == 1", r["channels"] == 1, str(r["channels"]))
    m, s = bg_stats(im, BOX)
    check("background is much closer to white than the raw gradient",
          m > 200, f"mean {m:.1f} (was {m_before:.1f})")
    check("background is flatter than the raw gradient", s < s_before,
          f"std {s:.2f} (was {s_before:.2f})")
    check("text is still dark (not washed out)", float((im < 100).mean()) > 0.02,
          f"{float((im < 100).mean()):.1%} dark")

print()
print("8. shadow removal works (MEASURED, not eyeballed)")
st, r = post("/enhance", {"image": page_b64, "preset": "original", "shadow_removal": True})
check("status 200", st == 200, f"got {st}")
if st == 200:
    im = decode(r)
    check("shadow_removal echoed as true", r["shadow_removal"] is True)
    m_after, s_after = bg_stats(im, BOX)
    print()
    print("     BACKGROUND STANDARD DEVIATION (the actual measurement)")
    print(f"       before   mean={m_before:8.3f}   std={s_before:8.4f}")
    print(f"       after    mean={m_after:8.3f}   std={s_after:8.4f}")
    print(f"       reduction: {s_before:.4f} -> {s_after:.4f}  "
          f"({100 * (1 - s_after / s_before):.2f}% lower)")
    print()
    check("background std-dev dropped by more than 90%",
          s_after < s_before * 0.10, f"{s_before:.4f} -> {s_after:.4f}")
    check("background lands essentially at pure white", m_after >= 245.0,
          f"{m_before:.2f} -> {m_after:.2f} (target >= 245)")
    check("image size unchanged", im.shape[:2] == page.shape[:2], f"{im.shape}")

    # Every preset combined with shadow_removal must still work.
    for p in PRESETS:
        stp, rp = post("/enhance", {"image": page_b64, "preset": p, "shadow_removal": True})
        okp = stp == 200
        if okp:
            ims = decode(rp)
            mp, sp = bg_stats(ims, BOX)
            check(f"  preset '{p}' + shadow_removal -> 200 and flatter background",
                  sp < s_before * 0.10, f"std {sp:.4f} vs {s_before:.4f}")
        else:
            check(f"  preset '{p}' + shadow_removal -> 200", False, f"status {stp}")

print()
print("   8b. invalid presets / parameters -> 422")
for name, body in (
    ("unknown preset", {"preset": "magic"}),
    ("preset not a string", {"preset": 7}),
    ("quality 0", {"quality": 0}),
    ("quality 101", {"quality": 101}),
    ("shadow_removal not a bool", {"shadow_removal": "yes please"}),
    ("output_format bogus", {"output_format": "tiff"}),
):
    st, rr = post("/enhance", {"image": page_b64, **body})
    check(f"422  {name}", st == 422, f"got {st}")

print()
print("   8c. /enhance guardrails match /scan exactly")
for label, data, want in (
    ("413 pixel bomb", base64.b64encode((ARTIFACTS / "fixtures" / "huge.jpg").read_bytes()).decode(), 413),
    ("400 corrupt", base64.b64encode((ARTIFACTS / "fixtures" / "corrupt.jpg").read_bytes()).decode(), 400),
    ("415 unsupported", base64.b64encode(b"II\x2a\x00" + bytes(range(200))).decode(), 415),
):
    st, rr = post("/enhance", {"image": data})
    check(f"{label} enforced", st == want, f"got {st}")

st, _ = post("/enhance", {"image": page_b64}, key="wrong-key")
check("401 on a bad API key", st == 401, f"got {st}")

st, rr = post("/enhance", {"image": base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(50)).decode()})
check("400 on a truncated PNG", st == 400, f"got {st}")

# 12MB body cap - a REAL over-cap request, not a "maybe" assertion.
# /scan par Phase A ne ye 413 verify kiya tha; /enhance ko bhi yehi guard
# milna chahiye, warna 12MB ka doosra darwaza khul jaayega.
big = b64(np.random.default_rng(3).integers(0, 255, (1400, 1400, 3), dtype=np.uint8), ".png")
huge = {"image": big, "preset": "bw"}
body_mb = len(json.dumps(huge)) / 1024 / 1024
print(f"      (random-noise payload is {body_mb:.1f} MB of JSON)")
st, _ = post("/enhance", huge)
check("oversized body still 413 or accepted by pixel limit", st in (200, 413), f"got {st}")

# Same image, forced over 12MB regardless of encoder luck: a valid 4000x3000
# page plus a long filler field. If this ever returns 200 the cap is gone.
filler = {"image": big, "preset": "bw", "note": "x" * (13 * 1024 * 1024)}
filler_mb = len(json.dumps(filler)) / 1024 / 1024
st, rr = post("/enhance", filler)
check(f"definitely-over-cap body ({filler_mb:.1f} MB) -> 413", st == 413, f"got {st}")
check("over-cap rejection mentions the size limit",
      st != 413 or "12" in str(rr.get("detail", "")),
      str(rr.get("detail", ""))[:90])

print()
print("   8d. /enhance timing at 2000px")
timings = {}
for p in PRESETS:
    runs = []
    for _ in range(3):
        st, rr = post("/enhance", {"image": page_b64, "preset": p})
        if st == 200:
            runs.append(rr["timing_ms"])
    if runs:
        t = runs[len(runs) // 2]
        timings[p] = t
        print(f"     {p:14s} decode {t['decode']:7.2f}  enhance {t['enhance']:7.2f}  "
              f"encode {t['encode']:6.2f}  total {t['total']:7.2f}   (ms, median of 3)")
check("all five presets report timings", len(timings) == 5, f"{sorted(timings)}")

# 2000px exactly
p2000 = cv2.resize(page, (2000, 1375), interpolation=cv2.INTER_AREA)
p2000_b64 = b64(p2000)
print(f"     --- at exactly 2000px long side ({p2000.shape[1]}x{p2000.shape[0]}) ---")
for p in PRESETS:
    runs = []
    for _ in range(3):
        st, rr = post("/enhance", {"image": p2000_b64, "preset": p})
        if st == 200:
            runs.append(rr["timing_ms"])
    if runs:
        t = runs[len(runs) // 2]
        print(f"     {p:14s} decode {t['decode']:7.2f}  enhance {t['enhance']:7.2f}  "
              f"encode {t['encode']:6.2f}  total {t['total']:7.2f}   (ms, median of 3)")

print()
print("   8e. artifacts written (NOT committed)")
artifacts = []


def artifact(img, name):
    a = save(img, name)
    artifacts.append((name, a))


artifact(page, "00_source_gradient_page.png")
st, r = post("/enhance", {"image": page_b64, "preset": "original", "shadow_removal": True})
if st == 200:
    artifact(decode(r), "01_original_shadow_removed.png")
for p in ("grayscale", "bw", "bw_adaptive", "clean_white"):
    st, rr = post("/enhance", {"image": page_b64, "preset": p})
    if st == 200:
        artifact(decode(rr), f"02_{p}.png")
    st, rr = post("/enhance", {"image": page_b64, "preset": p, "shadow_removal": True})
    if st == 200:
        artifact(decode(rr), f"03_{p}_shadow_removed.png")

phone = REPO / "test_fixtures" / "phone_photo.jpg"
if phone.exists():
    print()
    print("   8f. enhancement pipeline against test_fixtures/phone_photo.jpg")
    pb = base64.b64encode(phone.read_bytes()).decode()
    st, rr = post("/scan", {"image": pb, "max_side": 2000,
                            "corners": [[0.12, 0.11], [0.90, 0.15], [0.87, 0.90], [0.14, 0.87]]})
    check("phone_photo -> /scan 200", st == 200, f"got {st}")
    if st == 200:
        warped = cv2.imdecode(
            np.frombuffer(base64.b64decode(rr["warped_b64"]), np.uint8), cv2.IMREAD_COLOR)
        artifact(warped, "10_phone_photo_warped_page.png")
        print(f"     warped page {warped.shape[1]}x{warped.shape[0]}")
        print(f"     /scan timing_ms: {rr['timing_ms']}")
        wb = b64(warped)
        wbox = (20, warped.shape[0] - 90, warped.shape[1] - 20, warped.shape[0] - 20)
        wm0, ws0 = bg_stats(warped, wbox)
        for p in PRESETS:
            st2, rr2 = post("/enhance", {"image": wb, "preset": p, "shadow_removal": True})
            ok2 = st2 == 200
            if ok2:
                im2 = decode(rr2)
                artifact(im2, f"11_phone_{p}_shadow_removed.png")
                print(f"     {p:14s} timing_ms {rr2['timing_ms']}")
            check(f"  phone_photo /enhance '{p}' -> 200", ok2, f"got {st2}")
        st2, rr2 = post("/enhance", {"image": wb, "preset": "original", "shadow_removal": True})
        if st2 == 200:
            wm1, ws1 = bg_stats(decode(rr2), wbox)
            print(f"     phone background std: before {ws0:.3f} -> after {ws1:.3f}")
else:
    print()
    print("   8f SKIP  test_fixtures/phone_photo.jpg missing")

print()
print("   ARTIFACT PATHS")
for name, a in artifacts:
    print(f"     {name:44s} {a}")

print()
print("=" * 74)
failed = [n for n, ok in RESULTS if not ok]
print(f"TOTAL: {len(RESULTS)}   PASS {len(RESULTS) - len(failed)}   FAIL {len(failed)}")
if failed:
    print("FAILURES:")
    for n in failed:
        print(f"  - {n}")
sys.exit(1 if failed else 0)