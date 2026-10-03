"""Review points 1, 2, 6 ke liye targeted tests.

Order matters: POINT 2 apna rate-limit budget jaanbujhkar khaali kar deta hai,
isliye usse aakhir mein rakha hai.

Rate limit 30/min ka matlab hai "kisi bhi 60 second ke sliding window mein 30 se
zyada admitted requests nahi", poori suite ke total ka 30 nahi. Purana assertion
(`total_allowed == 30`, poore run ka cumulative non-429 count) galat tha: suite
60 second se lambha chalne par purane requests window se bahar nikal jaate hain
aur budget free ho jaati hai, jisse cumulative count 30 cross kar deta hai bina
kisi violation ke. Ye test asli contract check karta hai - har request ka
wall-clock time record karke har 60 second window ka count.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths
import base64
import json
import sys
import time
import urllib.error
import urllib.request as rq

BASE = _paths.API
KEY = _paths.KEY
FIX = _paths.FIX

fails = []
VALID_OK = []  # valid API key ke saath /scan calls: (status, monotonic time)
WINDOW = 60.0


def worst_window(entries, window=WINDOW):
    """Ek sliding window mein sabse zyada kitne non-429 requests ghusse.

    Server `len(times) >= RATE_LIMIT_PER_MIN` reject karta hai, yaani 30 ke
    andar admit karta hai. Ye function client side par wahi count nikalta hai,
    timestamps ke basis par - run ki total duration se independent.
    """
    times = sorted(t for st, t in entries if st != 429)
    worst = 0
    j = 0
    for i in range(len(times)):
        while times[i] - times[j] >= window:
            j += 1
        worst = max(worst, i - j + 1)
    return worst


def check(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}   {extra}")
    if not cond:
        fails.append(label)


def post(name, key=KEY, **extra):
    """POST /scan. Transport hiccups par dobara try karta hai.

    Ye zaroori hai: podman ke port-forward kabhi kabhi connection reset kar
    deta hai, jisse `urllib` ka `status = 0` milta hai. Purana code us 0 ko
    "admitted" maan leta tha (kyunki woh 429 nahi tha), jisse rate-limit count
    galat se 30 se upar chala gaya tha - server galat nahi, test harness
    apna hi crash count kar raha tha. Ab retried, aur agar phir bhi transport
    error bache to wo clearly report hota hai.
    """
    with open(f"{FIX}/{name}", "rb") as f:
        body = {"image": base64.b64encode(f.read()).decode(), **extra}
    status, payload = 0, {}
    for attempt in range(3):
        req = rq.Request(BASE + "/scan", data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json", "X-API-Key": key},
                         method="POST")
        try:
            with rq.urlopen(req, timeout=180) as r:
                status, payload = r.status, json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            try:
                status, payload = e.code, json.loads(e.read())
            except Exception:
                status, payload = e.code, {}
            break
        except Exception as e:
            status, payload = 0, {"transport": f"{e} (attempt {attempt + 1}/3)"}
            time.sleep(0.5)
    if key == KEY:
        VALID_OK.append((status, time.monotonic()))
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

st, _ = post("photo.png")
check("honest 800x450 still accepted", st == 200, f"-> {st}")
st, _ = post("big.png")
check("honest 4000x3000 (12M px < 50M) accepted", st == 200, f"-> {st}")

print()
print("=" * 74)
print("POINT 6: timing split (decode / scan / encode)")
print("=" * 74)
print("  Server-side, response ke timing_ms field se. 3 runs ka average.")
for f, dims in [("photo.png", "800x450"), ("flat.png", "1600x900"), ("big.png", "4000x3000")]:
    post(f)
    rows = [post(f)[1]["timing_ms"] for _ in range(3)]
    avg = {k: sum(r[k] for r in rows) / len(rows) for k in rows[0]}
    print(f"  {f:10s} ({dims:9s})  body_read {avg['body_read']:6.1f}  b64 {avg['b64_decode']:6.1f}  "
          f"decode {avg['decode']:6.1f}  scan {avg['scan']:6.1f}  encode {avg['encode']:6.1f}  "
          f"total {avg['total']:6.1f}   (ms)")
    check(f"{f} timing fields present",
          all(k in rows[0] for k in ("decode", "scan", "encode", "b64_decode", "body_read", "total")))

st, b = post("photo.png", method="adaptive")
if st == 200:
    t = b["timing_ms"]
    print(f"  photo.png  (adaptive)   decode {t['decode']:6.1f}  scan {t['scan']:6.1f}  "
          f"encode {t['encode']:6.1f}  total {t['total']:6.1f}   (ms)")

print()
print("=" * 74)
print("POINT 2: rate limit keyed on API key, NOT IP")
print("=" * 74)
print("  Sab requests SAME IP se. Agar limit IP pe hoti, to 40 galat-key")
print("  requests 429 dete aur valid key ka budget bhi khatam ho jaata.")

before = len([s for s, _ in VALID_OK if s == 200])
codes = [post("flat.png", key="totally-wrong-key")[0] for _ in range(40)]
after = len([s for s, _ in VALID_OK if s == 200])
check("40 wrong-key requests -> all 401, zero 429",
      codes.count(401) == 40 and codes.count(429) == 0, f"401={codes.count(401)} 429={codes.count(429)}")
check("valid key's budget untouched by wrong-key flood",
      before == after, f"valid 200s before={before} after={after}")

seq = [post("flat.png")[0] for _ in range(32)]
# Rate limiter un requests ko count karta hai jo limit ke andar ghare; 413/422 bhi
# uss bucket mein count hote hain (pixel check baad mein aata hai). Isliye 200 nahi,
# "429 ke alawa" count karna hai.
codes_all = [st for st, _ in VALID_OK]
transport = sum(1 for st in codes_all if st == 0)
peak = worst_window(VALID_OK)
span = VALID_OK[-1][1] - VALID_OK[0][1]
print(f"    valid-key sequence: 200s={seq.count(200)} 429s={seq.count(429)}")
print(f"    whole suite ran {span:.1f}s, {len(codes_all)} valid-key requests total")
print(f"    busiest 60s window anywhere in the suite: {peak} admitted")
print(f"      (of all admitted: {codes_all.count(413)} were pixel-limit 413s, "
      f"{codes_all.count(200)} were 200s)")
check("no transport failures anywhere in the suite", transport == 0,
      f"{transport} request(s) never reached the server even after 3 tries")
check("limit engages", seq.count(429) > 0, f"429s={seq.count(429)}")
check("no 60s window ever exceeded 30 admitted requests", peak <= 30, f"peak={peak} (max 30)")
check("limit is actually tight, not just permissive", peak >= 25, f"peak={peak} (want >=25, "
      f"taaki test genuinely 30 ke paas ja raha ho)")
check("the 32-request burst straddles the boundary and gets cut",
      seq.count(429) >= 1, f"429s={seq.count(429)}")

with rq.urlopen(rq.Request(BASE + "/health"), timeout=20) as r:
    check("/health unaffected while /scan is 429", r.status == 200)

print()
print("=" * 74)
print("FAILURES:", len(fails), fails)
print("=" * 74)
sys.exit(1 if fails else 0)