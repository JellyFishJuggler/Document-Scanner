"""Review points 1, 2, 6 ke liye targeted tests.

Order matters: POINT 2 apna rate-limit budget jaanbujhkar khaali kar deta hai,
isliye usse aakhir mein rakha hai. Valid-key requests ka cumulative count track
hota hai taaki "exactly 30/min" prove ho sake.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths
import base64
import json
import sys
import urllib.error
import urllib.request as rq

BASE = _paths.API
KEY = _paths.KEY
FIX = _paths.FIX

fails = []
VALID_OK = []  # valid API key ke saath /scan calls jo budget ke andar thi (200)


def check(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}   {extra}")
    if not cond:
        fails.append(label)


def post(name, key=KEY, **extra):
    with open(f"{FIX}/{name}", "rb") as f:
        body = {"image": base64.b64encode(f.read()).decode(), **extra}
    req = rq.Request(BASE + "/scan", data=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json", "X-API-Key": key},
                     method="POST")
    try:
        with rq.urlopen(req, timeout=180) as r:
            status, payload = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            status, payload = e.code, json.loads(e.read())
        except Exception:
            status, payload = e.code, {}
    except Exception as e:
        status, payload = 0, {"transport": str(e)}
    if key == KEY:
        VALID_OK.append(status)
    return status, payload


print("=" * 74)
print("POINT 1: decoded-pixel limit (decompression bomb)")
print("=" * 74)
print("  Files CHHOTI hain, header me BADE dimensions. 1MB se kam file par 4.8GB.")
for f, claim in [("huge.jpg", "40000x40000 = 1.6 BILLION px -> 4.8GB BGR"),
                 ("big_hdr.jpg", "20000x20000 = 400 MILLION px -> 1.2GB BGR"),
                 ("huge.png", "40000x40000 = 1.6 BILLION px -> 4.8GB BGR")]:
    st, b = post(f)
    check(f"{f:12s} {claim}", st == 413, f"-> {st} | {str(b.get('detail',''))[:70]}")

st, _ = post("real.png")
check("honest 800x450 still accepted", st == 200, f"-> {st}")
st, _ = post("big.png")
check("honest 4000x3000 (12M px < 50M) accepted", st == 200, f"-> {st}")

print()
print("=" * 74)
print("POINT 6: timing split (decode / scan / encode)")
print("=" * 74)
print("  Server-side, response ke timing_ms field se. 3 runs ka average.")
for f, dims in [("real.png", "800x450"), ("flat.png", "1600x900"), ("big.png", "4000x3000")]:
    post(f)
    rows = [post(f)[1]["timing_ms"] for _ in range(3)]
    avg = {k: sum(r[k] for r in rows) / len(rows) for k in rows[0]}
    print(f"  {f:10s} ({dims:9s})  body_read {avg['body_read']:6.1f}  b64 {avg['b64_decode']:6.1f}  "
          f"decode {avg['decode']:6.1f}  scan {avg['scan']:6.1f}  encode {avg['encode']:6.1f}  "
          f"total {avg['total']:6.1f}   (ms)")
    check(f"{f} timing fields present",
          all(k in rows[0] for k in ("decode", "scan", "encode", "b64_decode", "body_read", "total")))

st, b = post("real.png", method="adaptive")
if st == 200:
    t = b["timing_ms"]
    print(f"  real.png   (adaptive)   decode {t['decode']:6.1f}  scan {t['scan']:6.1f}  "
          f"encode {t['encode']:6.1f}  total {t['total']:6.1f}   (ms)")

print()
print("=" * 74)
print("POINT 2: rate limit keyed on API key, NOT IP")
print("=" * 74)
print("  Sab requests SAME IP se. Agar limit IP pe hoti, to 40 galat-key")
print("  requests 429 dete aur valid key ka budget bhi khatam ho jaata.")

before = len([s for s in VALID_OK if s == 200])
codes = [post("flat.png", key="totally-wrong-key")[0] for _ in range(40)]
after = len([s for s in VALID_OK if s == 200])
check("40 wrong-key requests -> all 401, zero 429",
      codes.count(401) == 40 and codes.count(429) == 0, f"401={codes.count(401)} 429={codes.count(429)}")
check("valid key's budget untouched by wrong-key flood",
      before == after, f"valid 200s before={before} after={after}")

seq = [post("flat.png")[0] for _ in range(32)]
# Rate limiter un requests ko count karta hai jo limit ke andar ghare; 413/422 bhi
# uss bucket mein count hote hain (pixel check baad mein aata hai). Isliye 200 nahi,
# "429 ke alawa" count karna hai.
total_allowed = len([s for s in VALID_OK if s != 429])
print(f"    valid-key sequence: 200s={seq.count(200)} 429s={seq.count(429)}")
print(f"    cumulative valid-key requests that passed the limit: {total_allowed}")
print(f"      (of which {VALID_OK.count(413)} were pixel-limit 413s, {VALID_OK.count(200)} were 200s)")
check("limit engages", seq.count(429) > 0, f"429s={seq.count(429)}")
check("exactly 30 allowed per minute per key", total_allowed == 30, f"= {total_allowed} (want 30)")

with rq.urlopen(rq.Request(BASE + "/health"), timeout=20) as r:
    check("/health unaffected while /scan is 429", r.status == 200)

print()
print("=" * 74)
print("FAILURES:", len(fails), fails)
print("=" * 74)
sys.exit(1 if fails else 0)