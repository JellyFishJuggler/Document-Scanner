"""Phase 2 HTTP tests against the running container. Pure stdlib urllib, no deps."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths
import base64
import json
import sys
import urllib.error
import urllib.request
import urllib.request as rq

BASE = _paths.API
KEY = _paths.KEY
FIX = _paths.FIX

fails = []
SCAN_CALLS = []   # har /scan request ka status
SCAN_VALID = []   # unme se sirf valid key wale (rate limit inhi pe lagta hai)


def call(path, body=None, key=KEY, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = rq.Request(BASE + path, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with rq.urlopen(req, timeout=timeout) as r:
            status, payload = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw[:200].decode("utf-8", "replace")}
    except (urllib.error.URLError, OSError) as e:
        status, payload = 0, {"detail": f"transport: {e}"}
    if path == "/scan":
        SCAN_CALLS.append(status)
        if key == KEY:
            SCAN_VALID.append(status)
    return status, payload


def b64(name):
    with open(f"{FIX}/{name}", "rb") as f:
        return base64.b64encode(f.read()).decode()


def check(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}   {extra}")
    if not cond:
        fails.append(label)


print("=" * 70)
print("1. auth")
print("=" * 70)
st, b = call("/health", key=None)
check("GET /health without key", st == 200, f"-> {st}")
st, b = call("/scan", {"image": b64("photo.png")}, key=None)
check("POST /scan without key -> 401", st == 401, f"-> {st} {b.get('detail','')[:40]}")
st, b = call("/scan", {"image": b64("photo.png")}, key="wrong-key")
check("POST /scan wrong key -> 401", st == 401, f"-> {st} {b.get('detail','')[:40]}")

print()
print("=" * 70)
print("2. happy path")
print("=" * 70)
st, b = call("/scan", {"image": b64("photo.png")})
check("POST /scan -> 200", st == 200, f"-> {st}")
if st == 200:
    print(f"    source_size {b['source_size']}  scan_size {b['scan_size']}")
    print(f"    detected={b['detected']}  used_fallback={b['used_fallback']}  method={b['method']}")
    print(f"    contour={b['contour']}")
    with open(f"{FIX}/out_photo.png", "wb") as f:
        f.write(base64.b64decode(b["scan_b64"]))
    check("response scan is decodable PNG", len(b["scan_b64"]) > 100)
    check("response has no file path / disk hint", "path" not in json.dumps(b).lower())

st, b = call("/scan", {"image": b64("photo.png"), "method": "adaptive"})
check("method=adaptive -> 200", st == 200, f"-> {st}")
if st == 200:
    with open(f"{FIX}/out_photo_adaptive.png", "wb") as f:
        f.write(base64.b64decode(b["scan_b64"]))
    check("adaptive differs from otsu", b["scan_b64"] != json.dumps("x"))

st, b = call("/scan", {"image": b64("photo.png"), "max_side": 400})
check("max_side=400 -> 200", st == 200, f"-> {st}")
if st == 200:
    check("max_side actually bounds output", b["scan_size"][0] <= 420, f"scan_size={b['scan_size']}")

print()
print("=" * 70)
print("3. EXIF orientation (no manual rotation code in api.py)")
print("=" * 70)
st, b = call("/scan", {"image": b64("exif6.jpg")})
check("exif6 -> 200", st == 200, f"-> {st}")
if st == 200:
    w, h = b["scan_size"]
    print(f"    stored w=1600 h=900, orientation=6")
    print(f"    server scan_size = w={w} h={h}")
    check("EXIF 6 applied by imdecode (portrait expected)", w == 900 and h == 1600)

st, b = call("/scan", {"image": b64("exif8.jpg")})
if st == 200:
    w, h = b["scan_size"]
    print(f"    stored w=900 h=1600, orientation=8")
    print(f"    server scan_size = w={w} h={h}")
    check("EXIF 8 applied by imdecode (landscape expected)", w == 1600 and h == 900)

st, b = call("/scan", {"image": b64("noexif.jpg")})
if st == 200:
    w, h = b["scan_size"]
    check("control (no EXIF) stays as stored", w == 1600 and h == 900, f"w={w} h={h}")

print()
print("=" * 70)
print("4. bad input")
print("=" * 70)
for label, body, want in [
    ("missing 'image' field", {"method": "otsu"}, 422),
    ("bad method value", {"image": b64("photo.png"), "method": "bogus"}, 422),
    ("max_side too small", {"image": b64("photo.png"), "max_side": 5}, 422),
    ("max_side too big", {"image": b64("photo.png"), "max_side": 99999}, 422),
    ("image not base64", {"image": "!!!!not base64!!!!"}, 400),
    ("image empty string", {"image": ""}, 400),
    ("corrupt jpeg bytes", {"image": b64("corrupt.jpg")}, 400),
]:
    st, b = call("/scan", body)
    check(f"{label} -> {want}", st == want, f"-> {st} {str(b.get('detail',''))[:45]}")

print()
print("=" * 70)
print("5. body cap (12MB)")
print("=" * 70)
big_b64 = "A" * (13 * 1024 * 1024)
st, b = call("/scan", {"image": big_b64})
check("oversized body -> 413", st == 413, f"-> {st} {str(b.get('detail',''))[:45]}")

print()
print("=" * 70)
print("6. rate limit (30/min) -- last, it consumes the budget")
print("=" * 70)
codes = []
for i in range(34):
    st, _ = call("/scan", {"image": b64("flat.png")})
    codes.append(st)
from collections import Counter
c = Counter(codes)
allowed = [s for s in SCAN_VALID if s != 429]
print("    section 6 status counts:", dict(c))
print(f"    valid-key /scan calls : {len(SCAN_VALID)}")
print(f"    of those, not 429     : {len(allowed)}")
print(f"    429s                  : {SCAN_VALID.count(429)}")
check("limit is exactly 30/min per API key", len(allowed) == 30, f"passed={len(allowed)} (want 30)")
check("extra requests got 429", SCAN_VALID.count(429) >= 4, f"429s={SCAN_VALID.count(429)}")
check("429 did NOT affect /health", call("/health", key=None)[0] == 200, "health stays open")

print()
print("=" * 70)
print("FAILURES:", len(fails), fails)
print("=" * 70)
sys.exit(1 if fails else 0)