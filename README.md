# Scanly

**Classical computer vision** document scanner: detect page edges, correct
perspective, binarize. No models, no training - just OpenCV geometry and
thresholding (Otsu, morphological close, contours, perspective transform).

App name is **Scanly**; this repo is the backend plus a **React Native** app.

| Kya | Kahan | File |
|---|---|---|
| Core pipeline | shared | `scanner.py` |
| HTTP API | Cloud container | `api.py` |
| Mobile app | Android/iOS device | `app/` *(bare React Native, stock template - Phase C)* |
| Local camera tool | Aapka laptop, phone ka camera adb se | `main.py` |

Core logic `scanner.py` mein hai, to scan ka output har jagah same hota hai - app
bhi `main.py` bhi dono yahi functions call karte hain.

```
scanner.py    pure functions: four_point_transform, resize_to_fit, scan_detection,
              binarize, scan_image, order_corners, validate_corners, prepare_page,
              estimate_background, remove_shadow, enhance
api.py        FastAPI server -> /health, /scan, /enhance
app/          React Native 0.87 app (Phase C)
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
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS ke liye allowed origins, comma-separated. **`*` allowed nahi** - server start hi nahi karega. Origins ke bina browser ka frontend API call nahi kar payega. |

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
  "max_decoded_pixels": 50000000,
  "allowed_origins": ["http://localhost:5173"]
}
```

### `POST /scan`

`X-API-Key` header zaroori. Body JSON:

```json
{
  "image": "<base64 JPEG>",
  "method": "otsu",
  "max_side": 2000,
  "corners": null
}
```

| Field | Default | Kya karta hai |
|---|---|---|
| `image` | - | base64 JPEG/PNG. **required** |
| `method` | `"otsu"` | `"otsu"` ya `"adaptive"` (blockSize 31, C 10) |
| `max_side` | `2000` | input image ke long side ki limit (CPU bound karne ke liye). Warped page hamesha poori resolution mein banta hai. |
| `corners` | `null` | 4 normalized `[x, y]` points (0..1), koi bhi order. Client ne Adjust Corners screen par drag kiye hue corners bhejta hai. Diye jaane par **automatic detection skip** ho jaati hai. `null` ya missing = auto-detect. |

`corners` validate hote hain, galat hua to **422**: exactly 4 `[x, y]` points,
`0..1` ke andar (0.02 ki chhoti tolerance - 2000px image par 40px), convex, aur
area kam se kam **5%** of the frame. Valid hone par server unhe khud
**TL/TR/BR/BL** order mein set kar deta hai, isiliye client koi bhi order bhej
sakta hai. Response ke `corners` wahi normalized TL/TR/BR/BL hain - chahe
detection se aaye ya client ke drag kiye hue nodes se.

Response:

```json
{
  "ok": true,
  "scan_b64": "<base64 PNG, 1-channel grayscale>",
  "scan_png_mime": "image/png",
  "used_fallback": false,
  "detected": true,
  "contour": [[200, 90], [600, 90], [600, 360], [200, 360]],
  "source_size": [800, 450],
  "scan_size": [400, 270],
  "method": "otsu",
  "timing_ms": {
    "body_read": 0.06,
    "b64_decode": 0.02,
    "decode": 4.25,
    "scan": 2.19,
    "encode": 1.07,
    "total": 7.82
  },

  "corners": [[0.25, 0.2], [0.75, 0.2], [0.75, 0.8], [0.25, 0.8]],
  "corners_source": "client",
  "warped_b64": "<base64 JPEG, the real COLOR page>",
  "warped_size": [400, 270],
  "warped_mime": "image/jpeg"
}
```

`scan_b64` aur `warped_b64` do alag cheezein hain:

| Field | Kya hai | Kis screen par |
|---|---|---|
| `warped_b64` | **asli color** warped page, JPEG q90 | Preview, aur Filters ka left side |
| `scan_b64` | wahi page B&W, 1-channel PNG | Filters ka right side, "Save" ke baad |
| `contour` | **pixel** coordinates (source image resolution mein) | debug |
| `corners` | **normalized** coordinates (0..1) | Adjust Corners screen ke drag nodes |

`timing_ms` server-side breakdown hai - app ke "Scanning..." screen ke liye
useful (sabse bada hissa `decode` hota hai, `scan` nahi).

`used_fallback: true` ka matlab document detect nahi hua aur poore frame ko scan
kiya gaya. Adjust Corners screen pe is case me user ko "Page not found" dikhana
chahiye, chaaron corners full-image boundaries par initialize karke, taaki wo
manually drag kar sake.

### `POST /enhance`

Warped page par ek filter. `/scan` ke **bilkul wahi** guardrails - auth, rate
limit, 12MB body cap, 12MB/50M-pixel limits, format allow-list - kyunki dono
endpoints ek hi `decode_and_guard()` helper call karte hain. Guardrails drift
nahi kar sakte.

```json
{
  "image": "<base64 JPEG ya PNG>",
  "preset": "clean_white",
  "shadow_removal": true,
  "output": "jpeg",
  "quality": null
}
```

| Field | Default | Kya karta hai |
|---|---|---|
| `image` | - | base64 JPEG/PNG. **required** |
| `preset` | `"original"` | ek neeche diya hua list |
| `shadow_removal` | `false` | desk shadow / uneven lighting hatao (kisi bhi preset ke saath) |
| `output` | `"jpeg"` | `"jpeg"` (chhota) ya `"png"` (lossless) |
| `quality` | `90` | JPEG quality 10-100. PNG par ignore hota hai. |

`output_format` bhi accept hota hai, par **deprecated** alias hai - naya code
`output` bheje. Dono ek saath aur alat-alat aaye to **422**.

| Preset | Output |
|---|---|
| `original` | color, as-is (bas optional shadow removal) |
| `grayscale` | **single channel** gray |
| `bw` | Otsu, strictly **0 aur 255** |
| `bw_adaptive` | adaptive threshold (blockSize 31, C 10) - uneven page par better |
| `clean_white` | background normalize karke paper safed, text dark |

Response:

```json
{
  "ok": true,
  "image_b64": "<base64>",
  "mime": "image/jpeg",
  "size": [800, 450],
  "channels": 1,
  "preset": "clean_white",
  "shadow_removal": true,
  "output": "jpeg",
  "source_size": [800, 450],
  "timing_ms": {
    "body_read": 0.36, "b64_decode": 0.39, "decode": 28.12,
    "shadow": 205.64, "preset": 31.69,
    "enhance": 240.36, "encode": 5.58, "total": 275.06
  }
}
```

`timing_ms` me har stage alag hai: `body_read`, `b64_decode`, `decode`, **`shadow`**
(background estimation), **`preset`** (actual filter), `encode`, `total`. `enhance`
= `shadow` + `preset` (Phase A compatibility). Ye zaroori hai - `clean_white` par
255ms me se ~205ms sirf background estimation me jaata hai, aur client ko ye
distinction dikhni chahiye.

**`bw` ke saath ek honest baat.** Algorithm ka output strictly 0/255 hai, par
default JPEG output lossy hai - har hard black/white edge par ringing aake
intermediate values (0-14, 240-255) bana deti hai. Agar client ko byte-exact
0/255 chahiye to `output: "png"` bhejo. Test dono verify karta hai.

**`clean_white` crease elimination nahi karta.** Wo sirf roshni ka variation flat
karta hai (background division). Ek sharp diagonal fold jo lighting nahi,
geometry hai, usse poori tarah nahi mita - isliye claim bhi nahi kiya jaata.

### Shadow removal: kya test hota hai, aur uski limit

**Sirf is synthetic test fixture par**, background standard deviation
**15.71 se 0.00** hua. Isse zyada kuch claim nahi kiya jaata - neeche poori
construction likhi hai taaki aap judge kar sakein ki ye number kitna
representative hai.

Fixture (poori tarah synthetic, koi photo nahi):

- Canvas `1600x1100`. Background ek **horizontal linear ramp**:
  `ramp[i] = linspace(234, 170, 1600)`, yaani column `x` ka har pixel same value
  hai (`234 - x * 64 / 1599`), aur wahi value teeno BGR channels me.
  Yani roshni **dheere dheere left se right** kam hoti hai, total 64 levels.
- Ink uske upar draw kiya gaya: title "QUARTERLY REPORT" (~y=190), ek rule
  (y=250), aur 15 text rows `y=330..756` - sab `x in [150, 1450]` ke andar.

Measurement (std-dev jis pixels par nikalte hain):

- Grayscale image ke rows `y=776..1084`, columns `x=120..1479` - ek
  `1360x309` px patch.
- Ye patch **poori tarah text se neeche** hai (last ink row `y=764`), isiliye
  usme sirf paper hai. Test ye khud verify karta hai: Otsu us box me
  `0.000%` ink deta hai.
- `numpy` float32 par us patch ka `mean()` aur `std()`.

Result: mean `201.50 → 253.00`, std-dev `15.71 → 0.00`.

**Iska matlab ye nahi ki asli shadows hamesha zero ho jaayenge.** Ye ek
controlled, perfectly linear, perfectly smooth gradient hai. Asli photos me
chhoti high-frequency texture, camera noise, paper ka grain, aur **crease**
(shadow nahi, geometry) hota hai - aur crease ko ye method flatten nahi karta.
Real photo par measured improvement bahut chhota hota hai (fixture test:
background std `9.68 → 7.41`). `clean_white` par bhi ye honest limit wahi hai:
wo roshni ka variation flat karta hai, crease nahi.

### Timings: 2000x1375, 3 runs ka average

Sab stage server-side alag-alag measure hote hain. `shadow_removal` off:

| Preset | decode | shadow | preset | encode | total |
|---|---|---|---|---|---|
| `original` | 32.8 | 0.0 | 0.0 | 7.2 | **41.6** |
| `grayscale` | 28.2 | 0.0 | 0.5 | 4.5 | **34.5** |
| `bw` | 28.1 | 0.0 | 3.6 | 4.7 | **38.2** |
| `bw_adaptive` | 29.2 | 0.0 | 22.2 | 4.4 | **57.4** |
| `clean_white` | 29.8 | 0.0 | **240.9** | 4.7 | **280.2** |

`shadow_removal=true`:

| Preset | decode | shadow | preset | encode | total |
|---|---|---|---|---|---|
| `original` | 31.4 | **214.6** | 0.0 | 7.1 | **254.7** |
| `grayscale` | 28.1 | **204.9** | 0.5 | 4.6 | **240.3** |
| `bw` | 28.5 | **208.4** | 3.0 | 5.1 | **246.3** |
| `bw_adaptive` | 28.4 | **209.7** | 24.5 | 4.7 | **274.4** |
| `clean_white` | 28.1 | 205.6 | 31.7 | 5.6 | **275.1** |

(`body_read` ~0.4ms aur `b64_decode` ~0.5ms dono rows me - network/parse
overhead, in tables se chhata kar diya hai.)

Teen baatein client ke liye:

1. `decode` har request ka sabse bada **fixed** cost hai (~28-33ms), chahe filter
   koi bhi ho.
2. Background estimation **~205-215ms** leti hai - ye morphological close +
   divide hai. `clean_white` isliye default mein `shadow=false` par bhi slow hai:
   apna background estimation khud karta hai (240ms `preset` stage me).
3. `clean_white` + `shadow_removal=true` double work nahi karta: preset stage
   31.7ms rehta hai kyunki pehle wala shadow step already flat kar chuka hai.

Isiliye "Processing document..." state zaroori hai - **progress percentage fake
nahi karni.**

### CORS

`ALLOWED_ORIGINS` se explicit list. Default `http://localhost:5173`.

- `*` allowed **nahi** hai - `ALLOWED_ORIGINS='*'` set karne par server start hi
  nahi hota (fail closed). Wildcard se koi bhi website background me hamare API
  key ke saath authenticated requests bhej sakti hai.
- Empty/whitespace list bhi reject hoti hai.
- Comma-separated multiple origins kaam karte hain.
- Allowed methods `GET, POST, OPTIONS`; allowed headers `Content-Type`,
  `X-API-Key`; `max-age=600`.

Browsers me CORS server ke response header se decide hota hai, isiliye
`curl` se origin check nahi hota - asli test browser semantics ko replicate
karta hai (`Origin` + `Access-Control-Request-*` headers bhejkar preflight).

### Errors

| Code | Kab |
|---|---|
| `401` | `X-API-Key` missing ya galat |
| `413` | body 12MB se badi, **ya** image `MAX_DECODED_PIXELS` se zyada |
| `415` | format JPEG/PNG nahi hai |
| `422` | body galat, ya `method`/`max_side`/`preset`/`output`/`quality`/`corners` invalid |
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
| B | API v1: corners override + warped preview, `/enhance`, CORS | done |
| C | React Native app (`app/`) | pending |
| D | Docs (`SCOPE.md` etc.) | pending |

**Frontend React Native hai - PWA/browser nahi.** `app/` ek bare React Native
project hai (RN 0.87.1, React 19.2, Hermes, new architecture enabled). Usme
native Android/iOS toolchain, `SafeAreaProvider` aur working debug build
already hain - inhe reuse karna hai, Expo par migrate karna nahi.

`app/` abhi stock template hai (ek `NewAppScreen`) - navigation, camera,
storage aur Scanly screens Phase C mein banenge. Koi personal photo
`app/` mein nahi hai.

Phase A ke baad `design/` (screen references) abhi repo mein nahi hai - Phase C
se pehle chahiye.

## Tests

Saare scripts `tests/` mein hain. Photos kabhi commit nahi hoti - fixtures runtime
par generate hote hain (`/tmp/scanly_artifacts`), aur real photos
`test_fixtures/` se padhi jaati hain (git-ignored).

```bash
podman build -t scanly:test .
podman run -d --name scanly-test -e API_KEY=test-secret-abc123 -p 18000:8000 scanly:test

# functional suites: bulk functional container, test-only rate limit
podman run -d --name scanly-test-b -e API_KEY=test-secret-abc123 \
  -e RATE_LIMIT_PER_MIN=100000 -p 18001:8000 scanly:test

.venv/bin/python tests/make_fixtures.py       # all fixtures SYNTHETIC
.venv/bin/python tests/make_phone_fixture.py  # A4 phone photo, EXIF orientation 6
.venv/bin/python tests/verify_phase1.py       # old-vs-new SHA256 parity
.venv/bin/python tests/verify_hash_table.py

# default rate limit (30/min) - real production behaviour
podman restart scanly-test
.venv/bin/python tests/test_api.py
podman restart scanly-test
.venv/bin/python tests/test_review.py         # pixel limit, rate limit, timings
podman restart scanly-test
.venv/bin/python tests/test_formats.py        # allow-list, SOF walking, EXIF

# bulk Phase B suites (functional container, limit raised for the run only)
SCANLY_TEST_URL=http://127.0.0.1:18001 .venv/bin/python tests/test_corners.py
SCANLY_TEST_URL=http://127.0.0.1:18001 .venv/bin/python tests/test_enhance.py
SCANLY_TEST_URL=http://127.0.0.1:18001 .venv/bin/python tests/test_cors.py
```

| Suite | Kya check karta hai |
|---|---|
| `verify_phase1.py` | Phase A refactor ka byte-level parity (0 failures) |
| `verify_hash_table.py` | resize on/off ka SHA256 table |
| `test_api.py` | happy path, auth, validation |
| `test_review.py` | pixel limit, compression bombs, rate limit, timings |
| `test_formats.py` | allow-list, SOF walking, EXIF |
| `test_corners.py` | corners override + validation, TL/TR/BR/BL ordering, 24 permutations, forced warp |
| `test_enhance.py` | all 5 presets, strict 0/255 (PNG), shadow measurement, timings, phone pipeline |
| `test_cors.py` | explicit origins, fail-closed on `*`, preflight, multi-origin |

Rate limit 30/min **per API key** hai, isliye do suites ke beech container restart
karna padta hai - warna `429` aa jaayenge. `scanly-test-b` sirf bulk functional
Phase B suites ke liye hai (corners 54 + enhance 140 + cors 27 assertions); wahan
limit relaxed hai taaki ek suite na toot jaye. Default 30/min production behaviour
`test_api.py`/`test_review.py` mein alag container par verify hota hai.

Generated images `/tmp/scanly_artifacts/` ke andar hain aur kabhi commit nahi
hote. Saare fixtures **synthetic** hain - koi personal photo copy ya commit nahi
hoti. `test_fixtures/phone_photo.jpg` bhi synthetic hai
(`tests/make_phone_fixture.py` se banti hai, 4000x3000 stored + EXIF orientation
6, jisse decode 3000x4000 hota hai).

## OCR?

Abhi nahi hai, aur v1 mein nahi hai. Tesseract (`pytesseract`) add karna aasan hai
par image upload latency barh jaati hai; v1.5 ke liye note kiya gaya hai.