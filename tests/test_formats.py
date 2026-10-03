"""Format allow-list, header-parser and error-mapping tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths
import base64, json, os, sys, urllib.error, urllib.request

import cv2, numpy as np

API = _paths.API
KEY = _paths.KEY
FIX = _paths.ARTIFACTS / "fmt"

os.makedirs(FIX, exist_ok=True)

# ----------------------------------------------------------------- fixtures
SOF_MARKERS = [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
               0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF]
NOT_SOF = [0xC4, 0xC8, 0xCC]


def page(w, h, label="TEST"):
    im = np.full((h, w, 3), 235, np.uint8)
    cv2.putText(im, label, (30, h // 2), cv2.FONT_HERSHEY_SIMPLEX,
                max(1.0, w / 500), 40, 3)
    return im


def enc(ext, img, params=None):
    ok, b = cv2.imencode(ext, img) if params is None else cv2.imencode(ext, img, params)
    assert ok, ext
    return b.tobytes()


def sof_offset(d):
    """Offset of the first real SOF marker in a JPEG."""
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


def patch_sof_marker(d, marker):
    b = bytearray(d)
    b[sof_offset(b) + 1] = marker
    return bytes(b)


def build_exif_with_thumbnail(thumb: bytes, main: bytes) -> bytes:
    """main ke pehle ek APP1/EXIF segment, jisme poori thumbnail JPEG hai.

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


def build_fixtures():
    F = {}
    base = page(800, 600)
    F["jpeg_ok"] = enc(".jpg", base)
    F["png_ok"] = enc(".png", base)
    F["jpeg_progressive"] = enc(".jpg", base, [int(cv2.IMWRITE_JPEG_PROGRESSIVE), 1])

    for ext, name in [(".webp", "webp"), (".bmp", "bmp"), (".tif", "tiff"), (".ppm", "ppm")]:
        F[name] = enc(ext, base)

    F["jpeg_empty"] = b""
    F["jpeg_soi_only"] = F["jpeg_ok"][:2]
    F["jpeg_trunc60"] = F["jpeg_ok"][: int(len(F["jpeg_ok"]) * 0.60)]
    F["jpeg_trunc2"] = F["jpeg_ok"][: max(2, int(len(F["jpeg_ok"]) * 0.02))]
    F["jpeg_soi_garbage"] = b"\xff\xd8" + os.urandom(300)
    F["jpeg_magic_garbage"] = b"\xff\xd8\xff" + os.urandom(300)
    F["random"] = os.urandom(500)
    F["random_ff"] = b"\xff" * 500

    F["png_trunc50"] = F["png_ok"][: int(len(F["png_ok"]) * 0.5)]
    F["png_header_only"] = F["png_ok"][:33]
    F["png_sig_garbage"] = b"\x89PNG\r\n\x1a\n" + os.urandom(40)

    F["jpeg_sof_lie"] = patch_sof(F["jpeg_ok"], 40000, 40000)
    p = bytearray(F["png_ok"])
    p[16:20] = (40000).to_bytes(4, "big")
    p[20:24] = (40000).to_bytes(4, "big")
    F["png_ihdr_lie"] = bytes(p)

    thumb = enc(".jpg", page(160, 120, "THUMB"))
    F["exif_thumb_lie"] = build_exif_with_thumbnail(thumb, patch_sof(F["jpeg_ok"], 40000, 40000))
    F["exif_thumb_ok"] = build_exif_with_thumbnail(thumb, F["jpeg_ok"])

    for name, data in F.items():
        with open(f"{FIX}/{name}.bin", "wb") as fh:
            fh.write(data)
    return F


def load():
    import ast
    src = open(_paths.REPO / "api.py").read()
    tree = ast.parse(src)
    fns = {"_jpeg_size", "_png_size", "read_image_size", "sniff_format", "_is_pixel_limit_error"}
    varz = {"_JPEG_SOF_MARKERS", "_JPEG_STANDALONE", "_JPEG_MAGIC", "_PNG_MAGIC", "_ALLOWED_FORMATS"}
    ns = {"cv2": cv2}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in fns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "api.py", "exec"), ns)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            if {t.id for t in tg if isinstance(t, ast.Name)} & varz:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "api.py", "exec"), ns)
    assert fns <= set(ns) and varz <= set(ns), (fns - set(ns), varz - set(ns))
    return ns


def post(name, data):
    body = json.dumps({"image": base64.b64encode(data).decode()}).encode()
    r = urllib.request.Request(API + "/scan", data=body,
                               headers={"Content-Type": "application/json", "X-API-Key": KEY},
                               method="POST")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw[:80].decode("utf8", "replace")}


PASS, FAIL = [], []


def check(label, got, want):
    (PASS if got == want else FAIL).append(label)
    print(f"  [{'PASS' if got == want else 'FAIL'}] {label}: got={got!r} want={want!r}")


def main():
    F = build_fixtures()
    api = load()
    size = api["read_image_size"]
    sniff = api["sniff_format"]
    ispix = api["_is_pixel_limit_error"]

    print("=" * 74)
    print("A. sniff_format() - allow-list")
    print("=" * 74)
    check("valid jpeg -> JPEG", sniff(F["jpeg_ok"]), "JPEG")
    check("valid png -> PNG", sniff(F["png_ok"]), "PNG")
    check("progressive jpeg -> JPEG", sniff(F["jpeg_progressive"]), "JPEG")
    for k in ("webp", "bmp", "tiff", "ppm", "random", "random_ff"):
        check(f"{k} -> None (415)", sniff(F[k]), None)
    check("empty -> None (400)", sniff(F["jpeg_empty"]), None)
    check("jpeg soi only (FFD8) -> None -> 415", sniff(F["jpeg_soi_only"]), None)
    check("jpeg soi+garbage (no 3rd FF) -> None -> 415", sniff(F["jpeg_soi_garbage"]), None)
    check("jpeg magic+garbage (FFD8FF) -> JPEG", sniff(F["jpeg_magic_garbage"]), "JPEG")
    check("png sig+garbage -> PNG", sniff(F["png_sig_garbage"]), "PNG")

    print("=" * 74)
    print("B. _jpeg_size() walks marker segments by LENGTH field (EXIF thumbnail)")
    print("=" * 74)
    # control: a naive 'first 0xFFC0 anywhere' scan WOULD be fooled
    naive = F["exif_thumb_lie"].find(b"\xff\xc0")
    nh = (F["exif_thumb_lie"][naive + 5] << 8) | F["exif_thumb_lie"][naive + 6]
    nw = (F["exif_thumb_lie"][naive + 7] << 8) | F["exif_thumb_lie"][naive + 8]
    print(f"  control: naive first-SOF scan returns {nw}x{nh} (the 160x120 thumbnail)")
    check("fixture is discriminating (naive gives thumbnail)", (nw, nh), (160, 120))
    check("length-walk skips APP1, finds main SOF", size(F["exif_thumb_lie"]), (40000, 40000))
    check("exif+normal jpeg -> true size", size(F["exif_thumb_ok"]), (800, 600))

    print("=" * 74)
    print("C. _jpeg_size() handles every SOF marker (SOF0..SOF15 except C4/C8/CC)")
    print("=" * 74)
    check("C4 not a SOF", 0xC4 in api["_JPEG_SOF_MARKERS"], False)
    check("C8 not a SOF", 0xC8 in api["_JPEG_SOF_MARKERS"], False)
    check("CC not a SOF", 0xCC in api["_JPEG_SOF_MARKERS"], False)
    for m in SOF_MARKERS:
        check(f"SOF marker 0x{m:02X}", size(patch_sof_marker(F["jpeg_ok"], m)), (800, 600))
    check("progressive SOF2 in file", F["jpeg_progressive"][sof_offset(F["jpeg_progressive"]) + 1], 0xC2)
    check("progressive parsed", size(F["jpeg_progressive"]), (800, 600))

    print("=" * 74)
    print("D. _is_pixel_limit_error() only matches the real assertion")
    print("=" * 74)
    real = cv2.error("OpenCV(5.0.0) /io/opencv/modules/imgcodecs/src/loadsave.cpp:79: "
                     "error: (-215:Assertion failed) pixels <= CV_IO_MAX_IMAGE_PIXELS "
                     "in function 'validateInputImageSize'")
    check("real pixel assertion -> True", ispix(real), True)
    for bad in ["Unsupported image format", "Corrupt JPEG data: premature end of data",
                "error: (-5:Bad argument) in function 'cvtColor'"]:
        check(f"non-pixel cv2.error -> False ({bad[:28]})", ispix(cv2.error(bad)), False)

    print("=" * 74)
    print("E. real phone photo: output/captured_frame.png")
    print("=" * 74)
    real_photo = open(_paths.REPO / "output" / "captured_frame.png", "rb").read()
    truth = cv2.imdecode(np.frombuffer(real_photo, np.uint8), cv2.IMREAD_COLOR)
    check("sniff -> PNG", sniff(real_photo), "PNG")
    check("header size == decoded size", size(real_photo), (truth.shape[1], truth.shape[0]))
    print(f"  -> {truth.shape[1]}x{truth.shape[0]} confirmed against a full decode")

    print("=" * 74)
    print("F. HTTP end-to-end")
    print("=" * 74)
    cases = [
        ("jpeg_ok", 200), ("png_ok", 200), ("jpeg_progressive", 200),
        ("exif_thumb_ok", 200),
        ("webp", 415), ("bmp", 415), ("tiff", 415), ("ppm", 415),
        ("random", 415), ("random_ff", 415),
        ("jpeg_empty", 400), ("jpeg_trunc60", 400),
        ("jpeg_trunc2", 400), ("jpeg_magic_garbage", 400),
        ("jpeg_soi_only", 415), ("jpeg_soi_garbage", 415),
        ("png_trunc50", 400), ("png_header_only", 400), ("png_sig_garbage", 400),
        ("jpeg_sof_lie", 413), ("png_ihdr_lie", 413), ("exif_thumb_lie", 413),
    ]
    for name, want in cases:
        code, body = post(name, F[name])
        detail = body.get("detail", "") if isinstance(body, dict) else ""
        check(f"POST {name}", code, want)
        if want in (413, 415):
            print(f"        detail: {detail}")

    print()
    print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())