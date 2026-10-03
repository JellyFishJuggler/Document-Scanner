"""Generate test_fixtures/phone_photo.jpg - a synthetic, non-personal phone JPEG.

Why this exists
---------------
Phase A item A4 asked for a genuine *normal* JPEG at realistic phone resolution to
prove `_jpeg_size()` reads the JPEG's SOF dimensions rather than the EXIF-oriented
shape. Every JPEG in the regression suite until now was a uniform-colour slab,
which would pass even with a parser that only looked at the first few bytes.

No personal photo is read, copied or referenced here. Every pixel is drawn from
OpenCV primitives: a lit desk, a sheet of paper at a slight angle, ruled text
lines, a soft shadow and sensor noise. Nothing is downloaded.

The EXIF orientation is deliberately **6**, not 1. Orientation 6 means "rotate 90
degrees clockwise for display", so the file stores 4000x3000 while OpenCV's
IMREAD_COLOR hands back a 3000x4000 array. That difference is the whole point: it
proves `_jpeg_size()` is reporting the real SOF fields instead of echoing back
whatever the decoder produced.

Do NOT "fix" the parser to make these two numbers agree. They are supposed to
differ.

Run:  .venv/bin/python tests/make_phone_fixture.py
"""
import struct
import sys

import cv2
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _paths import REPO, load_api_functions

# A real phone's main camera is a few megapixels; 12MP (4000x3000) is the single
# most common sensor size worldwide, so it is the honest "normal phone photo".
STORED_W, STORED_H = 4000, 3000
ORIENTATION = 6
JPEG_QUALITY = 88  # typical phone camera JPEG quality
OUT = REPO / "test_fixtures" / "phone_photo.jpg"


def exif_app1(orientation: int) -> bytes:
    """Minimal APP1/Exif segment carrying only Orientation (tag 0x0112).

    cv2 can decode EXIF but cannot write it, so the segment is built by hand.
    This is byte-for-byte the same helper tests/make_fixtures.py already uses to
    produce its orientation 6/8 fixtures.
    """
    tiff = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd = struct.pack("<H", 1)
    ifd += struct.pack("<H", 0x0112)          # tag: Orientation
    ifd += struct.pack("<H", 3)               # type: SHORT
    ifd += struct.pack("<I", 1)               # count
    ifd += struct.pack("<H", orientation) + b"\x00\x00"
    ifd += struct.pack("<I", 0)               # no next IFD
    payload = b"Exif\x00\x00" + tiff + ifd
    segment = b"\xFF\xE1" + struct.pack(">H", len(payload) + 2) + payload
    assert len(segment) == 36, len(segment)
    return segment


def build_scene(w: int, h: int) -> np.ndarray:
    """Draw a sheet of paper on a desk, lit from the upper left."""
    # Desk: a warm dark surface, blurred so it reads as out-of-focus background.
    desk = np.zeros((h, w, 3), np.uint8)
    desk[:] = (58, 74, 104)                                  # BGR slate/wood
    grain = np.random.default_rng(7).normal(0, 6, (h, w, 1))  # desk grain
    desk = np.clip(desk.astype(np.float32) + grain, 0, 255).astype(np.uint8)
    desk = cv2.GaussianBlur(desk, (0, 0), 9)

    # The page, tilted slightly so perspective detection has real work to do.
    page = np.array([
        [int(0.115 * w), int(0.100 * h)],
        [int(0.905 * w), int(0.145 * h)],
        [int(0.870 * w), int(0.905 * h)],
        [int(0.135 * w), int(0.870 * h)],
    ], np.int32)

    paper = np.full((h, w, 3), 246, np.uint8)

    # Text: a heading plus ruled body lines, both as filled rectangles so they
    # survive JPEG chroma subsampling the way real glyphs do.
    cv2.fillPoly(paper, [page], (246, 246, 244))
    ink = (38, 38, 40)

    def inside(p, margin=0.03):
        """A point margin-fraction of the way from the TL corner toward the BL."""
        return (int(p[0][0] * (1 - margin) + p[3][0] * margin),
                int(p[0][1] * (1 - margin) + p[3][1] * margin))

    x0, y0 = inside(page)
    x1, y1 = inside(page, 0.06)
    cv2.rectangle(paper, (x0 + 40, y0 + 50), (x0 + 1500, y0 + 120), ink, -1)
    for i in range(26):
        y = y0 + 240 + i * 68
        right = x1 - (120 if i % 7 == 6 else 0)
        cv2.rectangle(paper, (x0 + 40, y), (right - 40, y + 16), (70, 70, 74), -1)

    # Composite the page over the desk, then re-apply the paper gradient inside
    # the page mask so the sheet is not perfectly flat.
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [page], 255)
    mask = cv2.GaussianBlur(mask, (0, 0), 3).astype(np.float32) / 255.0

    lamp = np.linspace(1.0, 0.80, h, dtype=np.float32)[:, None]   # darker at bottom
    lamp = lamp * np.linspace(1.0, 0.92, w, dtype=np.float32)[None, :]
    shaded_paper = np.clip(paper.astype(np.float32) * lamp[..., None], 0, 255)

    out = desk.astype(np.float32) * (1 - mask[..., None]) + shaded_paper * mask[..., None]

    # Soft drop shadow along the lower-right edge of the page.
    shadow = cv2.GaussianBlur(mask.astype(np.float32) * 255, (0, 0), 45) / 255.0
    shadow = np.roll(np.roll(shadow, 34, axis=0), 34, axis=1) * (1 - mask / 255.0)
    out *= (1 - 0.42 * shadow[..., None])

    # Sensor noise + a gentle lens vignette.
    out += np.random.default_rng(11).normal(0, 2.6, out.shape)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    out *= (1 - 0.16 * np.clip(r - 0.45, 0, None))[..., None]

    return np.clip(out, 0, 255).astype(np.uint8)


def write_phone_jpeg(path, img, orientation):
    """Encode to JPEG and splice an APP1/EXIF segment in after SOI."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    assert ok, "cv2.imencode failed"
    data = buf.tobytes()
    assert data[:2] == b"\xff\xd8", "encoder did not produce a JPEG"
    out = data[:2] + exif_app1(orientation) + data[2:]
    path.write_bytes(out)
    return len(out)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    scene = build_scene(STORED_W, STORED_H)
    nbytes = write_phone_jpeg(OUT, scene, ORIENTATION)

    # --- verify the three things A4 actually asked for -----------------------
    # Loaded straight out of the real api.py - see _paths.load_api_functions.
    _jpeg_size = load_api_functions("_jpeg_size")["_jpeg_size"]

    raw = OUT.read_bytes()
    got = _jpeg_size(raw)
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)

    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  file size            {nbytes / 1024:.0f} KB")
    print(f"  EXIF orientation     {ORIENTATION}  (rotate 90 CW for display)")
    print()
    print("  A4 check 1 - _jpeg_size() must equal the real SOF dimensions")
    print(f"             _jpeg_size(...)          = {got}")
    print(f"             expected                 = ({STORED_W}, {STORED_H})")
    ok1 = got == (STORED_W, STORED_H)
    print(f"             {'PASS' if ok1 else 'FAIL'}")
    print()
    print("  A4 check 2 - decoded shape is rotated (this difference is EXPECTED)")
    print(f"             cv2.imdecode shape       = {decoded.shape}")
    print(f"             expected (h,w)           = ({STORED_W}, {STORED_H})")
    ok2 = decoded.shape[:2] == (STORED_W, STORED_H)
    print(f"             {'PASS' if ok2 else 'FAIL'}")
    print()
    print("  A4 check 3 - the two values are NOT equal, which is the whole point")
    print(f"             SOF {_jpeg_size(raw)} vs decoded w/h "
          f"({decoded.shape[1]}, {decoded.shape[0]}) - differ: {got != (decoded.shape[1], decoded.shape[0])}")
    print()
    assert ok1 and ok2, "A4 verification failed"


if __name__ == "__main__":
    main()