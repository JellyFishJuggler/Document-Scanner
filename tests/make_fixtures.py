"""Generate every fixture the test scripts need.

All fixtures are SYNTHETIC and generated at runtime, except `real.png`, which is a
copy of the one genuine phone photo in the repo (output/captured_frame.png). Real
photos from test_fixtures/ are read but never copied here.

Includes hand-built EXIF orientation=6/8 JPEGs. That is deliberate: the API refuses
to carry manual rotation code until there is proof that OpenCV's default
IMREAD_COLOR already applies the EXIF orientation, so the suite needs JPEGs that
genuinely carry the tag.
"""
import struct
import sys

import cv2
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _paths import FIX, REPO

SOF_MARKERS = [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
               0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF]


def exif_app1(orientation: int) -> bytes:
    """Minimal APP1/Exif segment carrying only Orientation (tag 0x0112)."""
    tiff = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd = struct.pack("<H", 1)
    ifd += struct.pack("<H", 0x0112)
    ifd += struct.pack("<H", 3)
    ifd += struct.pack("<I", 1)
    ifd += struct.pack("<H", orientation) + b"\x00\x00"
    ifd += struct.pack("<I", 0)
    payload = b"Exif\x00\x00" + tiff + ifd
    segment = b"\xFF\xE1" + struct.pack(">H", len(payload) + 2) + payload
    assert len(segment) == 36, len(segment)
    return segment


def write_exif_jpeg(path, img, orientation):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    data = buf.tobytes()
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    out = data[:2] + exif_app1(orientation) + data[2:]
    with open(path, "wb") as f:
        f.write(out)


def sof_offset(d):
    i = 2
    while i < len(d) - 1:
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = (d[i + 2] << 8) | d[i + 3]
        if m in SOF_MARKERS:
            return i
        i += 2 + ln
    raise AssertionError("no SOF found")


def patch_sof(d, w, h):
    b = bytearray(d)
    i = sof_offset(b)
    b[i + 5], b[i + 6] = (h >> 8) & 0xFF, h & 0xFF
    b[i + 7], b[i + 8] = (w >> 8) & 0xFF, w & 0xFF
    return bytes(b)


def build_exif_with_thumbnail(thumb: bytes, main: bytes) -> bytes:
    """A main JPEG preceded by an APP1/EXIF segment holding a whole thumbnail JPEG.

    TIFF layout (big-endian):
      0  'MM' 00 2A, IFD0 offset = 8
      8  IFD0: 1 entry -> ExifIFD pointer (0x8769) = 26, next = 0
      26 IFD1: 2 entries -> 0x0201 offset = 56, 0x0202 length
      56 thumbnail JPEG bytes
    """
    tiff = bytearray()
    tiff += b"MM\x00\x2a" + (8).to_bytes(4, "big")
    tiff += (1).to_bytes(2, "big")
    tiff += (0x8769).to_bytes(2, "big") + (4).to_bytes(2, "big") + (1).to_bytes(4, "big") + (26).to_bytes(4, "big")
    tiff += (0).to_bytes(4, "big")
    assert len(tiff) == 26, len(tiff)
    tiff += (2).to_bytes(2, "big")
    tiff += (0x0201).to_bytes(2, "big") + (4).to_bytes(2, "big") + (1).to_bytes(4, "big") + (56).to_bytes(4, "big")
    tiff += (0x0202).to_bytes(2, "big") + (4).to_bytes(2, "big") + (1).to_bytes(4, "big") + len(thumb).to_bytes(4, "big")
    tiff += (0).to_bytes(4, "big")
    assert len(tiff) == 56, len(tiff)
    tiff += thumb
    payload = b"Exif\x00\x00" + bytes(tiff)
    app1 = b"\xff\xe1" + (2 + len(payload)).to_bytes(2, "big") + payload
    return main[:2] + app1 + main[2:]


def main():
    real = cv2.imread(str(REPO / "output" / "captured_frame.png"))
    assert real is not None, "output/captured_frame.png missing"
    cv2.imwrite(str(FIX / "real.png"), real)
    print(f"real.png            {real.shape}  <- copy of the repo's phone photo")

    flat = np.full((900, 1600, 3), 235, np.uint8)
    cv2.imwrite(str(FIX / "flat.png"), flat)
    print(f"flat.png            {flat.shape}  uniform: contour area gate must reject -> full-frame fallback")

    write_exif_jpeg(FIX / "exif6.jpg", flat, 6)
    print("exif6.jpg           stored w=1600 h=900  orientation=6  expect decode w=900 h=1600")
    flat_p = np.full((1600, 900, 3), 235, np.uint8)
    write_exif_jpeg(FIX / "exif8.jpg", flat_p, 8)
    print("exif8.jpg           stored w=900  h=1600  orientation=8  expect decode w=1600 h=900")
    ok, nb = cv2.imencode(".jpg", flat, [cv2.IMWRITE_JPEG_QUALITY, 92])
    (FIX / "noexif.jpg").write_bytes(nb.tobytes())
    print("noexif.jpg          control, no EXIF at all")

    big = np.full((3000, 4000, 3), 40, np.uint8)
    page = np.array([[400, 300], [3600, 500], [3500, 2700], [500, 2500]], np.int32)
    cv2.fillConvexPoly(big, page, (240, 240, 240))
    for i in range(30):
        cv2.line(big, (700, 800 + i * 60), (700 + 2000 - (i % 5) * 300, 830 + i * 60), (50, 50, 55), 4)
    cv2.imwrite(str(FIX / "big.png"), big)
    print(f"big.png             {big.shape}")

    (FIX / "corrupt.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(200)))
    print("corrupt.jpg         garbage after SOI")

    # compression bombs: small files whose headers claim an enormous canvas
    ok, jb = cv2.imencode(".jpg", flat, [cv2.IMWRITE_JPEG_QUALITY, 92])
    jpg = jb.tobytes()
    (FIX / "huge.jpg").write_bytes(patch_sof(jpg, 40000, 40000))
    (FIX / "big_hdr.jpg").write_bytes(patch_sof(jpg, 20000, 20000))
    ok, pb = cv2.imencode(".png", flat)
    png = bytearray(pb.tobytes())
    png[16:20] = (40000).to_bytes(4, "big")
    png[20:24] = (40000).to_bytes(4, "big")
    (FIX / "huge.png").write_bytes(bytes(png))
    print(f"huge.jpg            {(FIX / 'huge.jpg').stat().st_size} bytes -> header claims 40000x40000 (1.6B px)")
    print(f"big_hdr.jpg         {(FIX / 'big_hdr.jpg').stat().st_size} bytes -> header claims 20000x20000 (400M px)")
    print(f"huge.png            {(FIX / 'huge.png').stat().st_size} bytes -> header claims 40000x40000")

    # EXIF thumbnail whose main SOF lies
    thumb_ok, tb = cv2.imencode(".jpg", np.full((120, 160, 3), 200, np.uint8))
    (FIX / "exif_thumb_lie.jpg").write_bytes(
        build_exif_with_thumbnail(tb.tobytes(), patch_sof(jpg, 40000, 40000)))
    (FIX / "exif_thumb_ok.jpg").write_bytes(build_exif_with_thumbnail(tb.tobytes(), jpg))
    print("exif_thumb_lie.jpg  160x120 thumbnail in APP1, main SOF says 40000x40000 -> must be 413")
    print("exif_thumb_ok.jpg   same shape, honest 1600x900 main -> must be 200")

    # progressive JPEG, genuinely encoded (not a patched baseline)
    ok, pb2 = cv2.imencode(".jpg", flat, [int(cv2.IMWRITE_JPEG_PROGRESSIVE), 1])
    (FIX / "progressive.jpg").write_bytes(pb2.tobytes())
    print("progressive.jpg     real SOF2 (progressive) encode")

    print()
    print("--- local ground truth for EXIF, decoded by the SAME cv2 in this venv ---")
    for name in ("exif6.jpg", "exif8.jpg", "noexif.jpg"):
        d = cv2.imdecode(np.frombuffer((FIX / name).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        print(f"  {name:12s} decoded shape={d.shape}  (w={d.shape[1]}, h={d.shape[0]})")
    print()
    print(f"fixtures in {FIX}")


if __name__ == "__main__":
    main()