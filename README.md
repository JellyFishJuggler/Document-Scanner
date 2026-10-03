# Scanly

Scanly is a document-scanning backend built with classical computer vision. Given a
JPEG or PNG image, it can detect a page, correct perspective, and return a color
preview and a black-and-white scan. It can also apply image-enhancement presets.

The repository contains a Python backend and an `app/` directory holding the bare
React Native starter template. The mobile app does not yet implement scanning or
connect to the backend. Phase C, the mobile application work, has not started.

## Current capabilities

- Classical OpenCV and NumPy image processing; no AI or machine learning.
- HTTP endpoints for health checks, scanning, and enhancement.
- Automatic page detection with a full-frame fallback, or caller-supplied corners.
- Otsu and adaptive thresholding for the scan result.
- Five enhancement presets: `original`, `grayscale`, `bw`, `bw_adaptive`, and
  `clean_white`.
- Explicit CORS origins and bounded request/image processing.

Scanly does not include OCR, cloud sync, user accounts, authentication, automatic AI
capture, confidence metrics, or unsupported document modes. It does not currently
include a functioning mobile scanner UI, camera integration, gallery picker, local
document library, or frontend API client. The React Native directory is still the
stock/bare template.

## Architecture

| File or directory | Responsibility |
|---|---|
| `scanner.py` | Computer-vision functions: detection, corner ordering and validation, perspective correction, binarization, and enhancement. |
| `api.py` | FastAPI endpoints, request validation, resource limits, image decoding/encoding, CORS, and orchestration. |
| `main.py` | Local camera scanning tool. |
| `mobile_camera.py` | Local phone-camera/ADB utility. |
| `app/` | Bare React Native template; no Scanly UI or backend integration yet. |
| `tests/` | Backend HTTP suites, offline regression checks, and runtime fixture generators. |

Images sent to the API are processed in memory. The backend does not intentionally
write uploaded images to disk or include image bytes in its logs.

## Scanner pipeline

The backend's automatic path downsizes an image for contour detection, converts it
to grayscale, blurs and thresholds it, then uses morphological operations and
contours to find a plausible quadrilateral. It orders the corners and applies a
perspective transform to flatten the page. If detection fails, it uses the whole
frame and marks `used_fallback` in the response. Clients may instead provide four
normalized corners; these are validated and ordered before use.

The scan output is binarized with Otsu thresholding by default or adaptive
thresholding when requested. `/enhance` offers these presets:

| Preset | Result |
|---|---|
| `original` | Color image, optionally with shadow removal. |
| `grayscale` | Single-channel grayscale image. |
| `bw` | Otsu black-and-white threshold. |
| `bw_adaptive` | Adaptive black-and-white threshold. |
| `clean_white` | Background normalization intended to make paper lighter and text darker. |

Optional shadow removal estimates and normalizes background illumination. It does
not remove physical creases. `bw` outputs are strictly 0/255 when encoded as PNG;
JPEG is lossy and may introduce intermediate pixel values.

## Backend API

The server listens on port `8000` by default. No API key, authentication header, or
`API_KEY` environment variable is used.

### `GET /health`

Returns service status and runtime information.

### `POST /scan`

Accepts a JSON body with a base64-encoded JPEG or PNG image. Optional fields include
`method` (`otsu` or `adaptive`), `max_side` (200–6000), and `corners` (four normalized
`[x, y]` points). The response includes `warped_b64` (color JPEG), `scan_b64`
(black-and-white PNG), corner and size information, detection/fallback status, and
timings.

### `POST /enhance`

Accepts a base64-encoded JPEG or PNG, a `preset`, optional `shadow_removal`, output
format (`jpeg` or `png`), and JPEG `quality` (10–100). Returns the enhanced image,
dimensions, channels, selected preset, and timings.

Malformed JSON or parameters return `422`; invalid/truncated image data returns a
client error; unsupported formats return `415`; oversized bodies or pixel counts
return `413`; rate-limited requests return `429`; and unavailable processing slots
return `503`.

## Validation and resource protections

Authentication has been removed because this is currently a single-user/mobile
project without accounts. These backend protections remain:

- Request bodies are capped at 12 MiB and drained without buffering after the cap.
- Only JPEG and PNG are accepted. Magic bytes are checked before decoding.
- Image dimensions are checked from JPEG/PNG headers before decode and verified
  after decode. The default limit is 50 million pixels; OpenCV's own image-pixel
  limit is set as an additional guard.
- A sliding-window rate limit defaults to 30 requests per minute per socket peer.
  The server does not trust arbitrary `X-Forwarded-For` values. Behind a reverse
  proxy, clients may share the proxy's bucket.
- At most two CPU-heavy scan/enhance operations run concurrently by default; a
  request that cannot acquire a slot within 30 seconds receives `503`.
- CORS uses explicit origins from `ALLOWED_ORIGINS`; wildcard and empty origin
  configurations prevent startup. CORS is a browser policy, not authentication.
- CPU-heavy image work is offloaded from the async event loop. Image content is not
  logged.

## Run the backend

Python dependencies are listed in `requirements.txt`.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python api.py
```

No API key is needed. Optional environment variables are `PORT` (default `8000`),
`MAX_DECODED_PIXELS` (default `50000000`), `RATE_LIMIT_PER_MIN` (default `30`),
`MAX_CONCURRENT_SCANS` (default `2`), and `ALLOWED_ORIGINS` (default
`http://localhost:5173`, comma-separated).

The backend can also be run in a container:

```bash
docker build -t scanly:local .
docker run --rm -p 8000:8000 scanly:local
```

## Tests

Fixtures are generated at runtime; the suites use synthetic images and the existing
local fixture where applicable. The two offline regression checks do not need a
running API server:

```bash
.venv/bin/python tests/verify_phase1.py
.venv/bin/python tests/verify_hash_table.py
```

The HTTP suites require the backend to be running. Generate fixtures first, then
run the suites against the configured URL (default `http://127.0.0.1:18000`):

```bash
.venv/bin/python tests/make_fixtures.py
.venv/bin/python tests/make_phone_fixture.py
PORT=18000 .venv/bin/python api.py
```

In a second terminal, run `test_api.py`, `test_review.py`, `test_corners.py`,
`test_enhance.py`, `test_cors.py`, and `test_formats.py` from `.venv`. Set
`SCANLY_TEST_URL` to the server URL as needed. Rate-limited suites should use a fresh
server process or allow the rate-limit window to expire between runs. `test_cors.py`
also builds and launches containers to check startup behavior; it requires Docker or
Podman and the `scanly:test` image built from this repository.

## Current status and limitations

Backend phases A and B and this cleanup are complete. Phase C (the React Native
scanning application) is not implemented and has not started. The app remains the
stock template; no native camera, navigation, storage, or API-client dependencies
have been added for Scanly.

Page detection can fail on low-contrast images, in which case the API falls back to
the whole frame. Perspective correction can stretch pages photographed at steep
angles. Shadow normalization handles illumination variation imperfectly and is
computationally expensive at large sizes. It does not remove creases. There is no
OCR, searchable output, PDF export, cloud storage, or cross-device backup.
