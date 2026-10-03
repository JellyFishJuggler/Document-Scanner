# Scanly

**Classical computer vision** document scanner: detect page edges, correct
perspective, binarize. No models, no training - just OpenCV geometry and
thresholding (Otsu, morphological close, contours, perspective transform).

App name is **Scanly**; this repo is the backend plus the web front end.

| Kya | Kahan | File |
|---|---|---|
| Core pipeline | shared | `scanner.py` |
| HTTP API | Cloud container | `api.py` |
| PWA front end | Vite dev server / static host | *(Phase C, not built yet)* |
| Local camera tool | Aapka laptop, phone ka camera adb se | `main.py` |

Core logic `scanner.py` mein hai, to scan ka output har jagah same hota hai.

```
scanner.py    pure functions: four_point_transform, resize_to_fit, scan_detection,
              binarize, scan_image
api.py        FastAPI server -> /health, /scan
main.py       local live camera loop (camera + 's' save + 'q' quit)
tests/        test suite (fixtures generated at runtime, no photos committed)
```

## Privacy

**Uploaded images kabhi disk pe store nahi hoti.** Poora kaam memory mein hota hai:
base64 decode -> OpenCV array -> scan -> PNG encode -> wapas bhejo. Server par koi
image file likhi nahi jaati, koi temporary file nahi banti, aur image data kabhi log
mein nahi jaata (logs mein sirf dimensions aur timing aati hai). Request ke khatam
hone par memory sab free ho jaata hai.

Jab tak `POST /scan` ka response nahi gaya, server par koi trace nahi rehta.

## Render ka free tier

Free Render instances **idle hone par sleep kar jaate hain**. Iska matlab: agar kuch
der koi request nahi aaya, to agla (pehla) request 30-50 second lag sakta hai jab tak
instance dobara start hota hai. Iske baad normal speed (roughly 100-200ms per scan)
reh jaati hai. Ye theek hai, bas app ke pehle scan mein "Scanning..." zyada der
dikhega - galti nahi hai.

Health check `/health` hai, jo Render khud use karta hai. Ye bina API key ke khula
rehta hai taaki platform ko status pata chale.

## Chalane ka tareeka

### Local

`API_KEY` zaroori hai - set nahi hui to server start hi nahi hoga (fail closed,
taaki galti se public scanner na khul jaye).

```bash
source .venv/bin/activate
pip install fastapi uvicorn          # venv mein sirf cv2/numpy hai
API_KEY=apna-secret python api.py
```

Phir `http://127.0.0.1:8000/docs` pe interactive docs khulenge.

### Docker

```bash
podman build -t scanly:test .               # docker build bhi chalega
podman run --rm -e API_KEY=apna-secret -p 8000:8000 scanly:test
```

### `main.py` (local camera)

Pehle phone par "IP Webcam" install karke uska server start karo, phir:

```bash
python main.py              # USB debugging se
python mobile_camera.py     # ya sirf camera test karna ho
```

## Env vars

| Var | Default | Kya karta hai |
|---|---|---|
| `API_KEY` | - | **zaroori.** `X-API-Key` header se compare hota hai. Set nahi to server start nahi hoga. |
| `PORT` | `8000` | Render isko randomly set karta hai |
| `MAX_DECODED_PIXELS` | `50000000` | 50M pixels. Header se check hota hai, decode se pehle |
| `RATE_LIMIT_PER_MIN` | `30` | **per API key** requests per minute (IP pe nahi - Render ke proxy peeche hain, aur `X-Forwarded-For` client khud bhar sakta hai) |
| `MAX_CONCURRENT_SCANS` | `2` | ek saath kitne scan chalenge (free instance ke liye chhota rakha hai) |

## API

### `GET /health`

Bina auth. Server + OpenCV/numpy versions batata hai, aur ye batata hai ki method
**classical computer vision** hai.

```json
{
  "ok": true,
  "service": "scanly",
  "version": "1.0.0",
  "method": "classical computer vision",
  "cv2": "5.0.0",
  "numpy": "2.5.3",
  "max_decoded_pixels": 50000000
}
```

### `POST /scan`

`X-API-Key` header zaroori. Body JSON:

```json
{
  "image": "<base64 JPEG>",
  "method": "otsu",
  "max_side": 2000
}
```

| Field | Default | Kya karta hai |
|---|---|---|
| `image` | - | base64 JPEG/PNG. **required** |
| `method` | `"otsu"` | `"otsu"` ya `"adaptive"` (blockSize 31, C 10) |
| `max_side` | `2000` | input image ke long side ki limit (CPU bound karne ke liye). Warped page hamesha poori resolution mein banta hai. |

Response:

```json
{
  "ok": true,
  "scan_b64": "<base64 PNG, 1-channel grayscale>",
  "used_fallback": false,
  "detected": true,
  "contour": [[582, 0], [527, 175], [0, 157], [0, 0]],
  "source_size": [800, 450],
  "scan_size": [582, 183],
  "method": "otsu",
  "timing_ms": {
    "body_read": 0.1,
    "b64_decode": 0.2,
    "decode": 4.0,
    "scan": 1.9,
    "encode": 0.9,
    "total": 7.0
  }
}
```

`timing_ms` server-side breakdown hai - app ke "Scanning..." screen ke liye
useful (sabse bada hissa `decode` hota hai, `scan` nahi).

`used_fallback: true` ka matlab document detect nahi hua aur poore frame ko scan
kiya gaya. App mein iske liye ek badge dikhana chahiye - warna user solega ki
document sahi pakda gaya.

### Errors

| Code | Kab |
|---|---|
| `401` | `X-API-Key` missing ya galat |
| `413` | body 12MB se badi, **ya** image `MAX_DECODED_PIXELS` se zyada |
| `415` | format JPEG/PNG nahi hai |
| `422` | body galat, ya `method`/`max_side` invalid |
| `429` | 30 requests/minute cross |
| `503` | server busy (30s me slot nahi mila) |

### Sirf JPEG aur PNG

Magic bytes se allow-list: JPEG `FF D8 FF`, PNG `89 50 4E 47 0D 0A 1A 0A`. Kuch
aur (WebP, BMP, TIFF, PPM) ya pehchan hi na ho sake (random bytes, 2-byte stub)
-> **415**.

Ye jaan-boojh kar tight hai. Agar WebP/TIFF allow karte, to unka header parse hi
nahi hota, aur pixel-limit ke liye sirf `OPENCV_IO_MAX_IMAGE_PIXELS` bachti -
jo `import cv2` ke order par tika hua ek single point of failure hai. Phone camera
JPEG bhejta hai, isliye baaki formats ka koi practical nuksan nahi.

Jab bytes JPEG/PNG identify ho gaye par phir bhi corrupt/truncated hon ->
**400** (galat image). Sirf pixel-limit wala case **413** hai; `cv2.error` ke
sabhi flavours 413 nahi hote, sirf wo jisme OpenCV khud `CV_IO_MAX_IMAGE_PIXELS`
assert karta hai.

### Image size aur "compression bomb"

12MB ka body cap **kaafi nahi** hai. Ek solid-colour image 12MB se bahut chhoti ho
sakti hai, par uska header `40000x40000` bol sakta hai - matlab 1.6 **billion**
pixels, jiske liye BGR array **4.8GB** chahiye. File chhoti, bomb badi.

Isliye `MAX_DECODED_PIXELS` (default 50M) teen jagah se lagta hai:

1. **Header se check, decode se pehle** (`read_image_size()`) - JPEG ka SOF aur PNG
   ka IHDR padhe jaate hain. Koi allocation nahi hoti, isliye bomb allocation hone
   se pehle hi 413 mil jaata hai, saaf message ke saath.
2. **OpenCV ka apna limit** (`OPENCV_IO_MAX_IMAGE_PIXELS`, `import cv2` se pehle
   set hota hai) - agar koi format header check se bach jaaye.
3. **Decode ke baad verify** - defense in depth.

50M pixels ka BGR array 150MB hota hai. 512MB wale Render instance pe
`MAX_CONCURRENT_SCANS=2` ke saath theek chalta hai, par chhota instance ho to
`MAX_DECODED_PIXELS` kam kar dena.

Test kiya gaya: 23KB ki JPEG jiska header `40000x40000` bolta hai -> **413**, aur
container ki RSS 68MB se badh kar bhi bomb wale request ke baad 108MB hi rahi
(container OOM-killed nahi hua, 0 restarts).

EXIF orientation khud handle hota hai (`cv2.IMREAD_COLOR` default use hota hai) -
phone ki rotated photos seedhi upright padhti hain, isiliye alag se rotation code
likhne ki zaroorat nahi padi.

## Kitna ban chuka hai

| Phase | Kya | Status |
|---|---|---|
| A | Backend hardening: format allow-list, pixel limit, error mapping, tests | done |
| B | API v1: corners override, `/enhance`, CORS | pending |
| C | PWA front end | pending |
| D | Docs (`SCOPE.md` etc.) | pending |

`design/` (screen PNGs + HTML) aur `test_fixtures/phone_photo.jpg` abhi repo mein
nahi hain. Phase C ke liye designs chahiye, aur `_jpeg_size` ko ek real phone JPEG
par verify karne ke liye wo fixture chahiye.

## Tests

Saare scripts `tests/` mein hain. Photos kabhi commit nahi hoti - fixtures runtime
par generate hote hain (`/tmp/scanly_artifacts`), aur real photos
`test_fixtures/` se padhi jaati hain (git-ignored).

```bash
podman build -t scanly:test .
podman run -d --name scanly-test -e API_KEY=test-secret-abc123 -p 18000:8000 scanly:test

.venv/bin/python tests/make_fixtures.py     # synthetic fixtures
.venv/bin/python tests/verify_phase1.py     # old-vs-new SHA256 parity
.venv/bin/python tests/verify_hash_table.py
podman restart scanly-test
.venv/bin/python tests/test_api.py
podman restart scanly-test
.venv/bin/python tests/test_review.py       # pixel limit, rate limit, timings
podman restart scanly-test
.venv/bin/python tests/test_formats.py      # allow-list, SOF walking, EXIF
```

Rate limit 30/min **per API key** hai, isliye do suites ke beech container restart
karna padta hai - warna `429` aa jaayenge.

## OCR?

Abhi nahi hai, aur v1 mein nahi hai. Tesseract (`pytesseract`) add karna aasan hai
par image upload latency barh jaati hai; v1.5 ke liye note kiya gaya hai.