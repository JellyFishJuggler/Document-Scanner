# Scanly

Scanly is a small browser-based document scanner. It turns a phone or computer
photo into a straightened color page that can be downloaded as a JPEG. The
backend uses classical computer vision through OpenCV; the browser handles image
capture, corner adjustment, API requests, and download.

The V1 flow is:

**Home → Camera or Upload → Adjust Corners → Scan → Preview → Download**

The same web app is served by FastAPI at `/`. There is no separate frontend build
or package installation.

## Current features

- Responsive home, camera, corner adjustment, and preview screens.
- Browser camera capture through `navigator.mediaDevices.getUserMedia()` with no
  microphone access.
- JPEG and PNG image upload.
- Four touch- and mouse-draggable page corners.
- Optional server-side re-detection and perspective correction through `POST /scan`.
- Color preview uses the backend's `warped_b64` result and downloads as a named JPEG.
- A small API client with request timeout, HTTP, network, and response validation.
- No accounts, authentication, cloud storage, or saved server-side files.

## Architecture

| Path | Responsibility |
|---|---|
| `web/` | Dependency-free HTML, CSS, and JavaScript browser app. |
| `api.py` | FastAPI routes, validation, resource limits, CORS, and static web serving. |
| `scanner.py` | OpenCV detection, corner validation, perspective transform, and enhancement functions. |
| `tests/` | Backend API and regression suites. |

The browser does not implement image detection or perspective transformation.
It sends the source image and normalized corners to the existing backend. The
backend remains the sole computer-vision implementation. No image is stored on
the server.

## Run locally

Python dependencies are listed in `requirements.txt`.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python api.py
```

Open [http://localhost:8000](http://localhost:8000) on the development computer.
FastAPI serves the web app and API from one origin, so the app uses relative API
routes by default. No API key is required.

Camera access requires a secure browser context. `localhost` is treated as
secure by modern browsers, so the camera can be tested on the same computer.
Opening `http://<computer-LAN-address>:8000` from a phone does not reliably grant
camera access. For phone testing, expose the local server through a trusted HTTPS
development tunnel or configure HTTPS on the development host, then open that
HTTPS URL on the phone. Image upload works without camera permission.

For a separately hosted frontend, set the `scanly-api-base` meta value in
`web/index.html` or define `window.SCANLY_API_BASE_URL` before `app.mjs` loads.
The FastAPI `ALLOWED_ORIGINS` setting must include that frontend's exact origin.
For production, serve the frontend and API from the same HTTPS origin where
possible.

## API

- `GET /health` — service health and runtime information.
- `POST /scan` — accepts a JSON body containing base64 `image`, optional
  normalized `corners`, and scan options. Returns `corners`, `corners_source`,
  `warped_b64`, `warped_size`, `warped_mime`, and `scan_b64`.
- `POST /enhance` — backend enhancement presets. The V1 browser flow does not
  include a filters screen.

The web app uses `/scan` for manual corner scans. “Re-detect” calls `/scan`
without corners and displays the returned page points. If the user continues
without moving those detected points, the returned color warp is reused. The
frontend primarily previews `warped_b64`; it does not reproduce the backend's
warp or detection logic.

The API retains request/body limits, JPEG/PNG allow-list and magic-byte checks,
pixel limits, malformed-input handling, rate limiting, concurrency protection,
and explicit-origin CORS. Rate limiting uses the server's socket peer and does
not trust arbitrary `X-Forwarded-For` headers.

## Tests

Run dependency-free frontend utility and API-client tests with Node.js:

```bash
node web/tests/frontend.test.mjs
```

The backend also has offline regression checks and HTTP API suites. With the
project's Python environment installed, generate fixtures and run the server:

```bash
.venv/bin/python tests/make_fixtures.py
.venv/bin/python tests/make_phone_fixture.py
PORT=18000 .venv/bin/python api.py
```

In a second terminal, run the HTTP suites:

```bash
SCANLY_TEST_URL=http://127.0.0.1:18000 .venv/bin/python tests/test_api.py
SCANLY_TEST_URL=http://127.0.0.1:18000 .venv/bin/python tests/test_review.py
SCANLY_TEST_URL=http://127.0.0.1:18000 .venv/bin/python tests/test_corners.py
SCANLY_TEST_URL=http://127.0.0.1:18000 .venv/bin/python tests/test_enhance.py
SCANLY_TEST_URL=http://127.0.0.1:18000 .venv/bin/python tests/test_formats.py
.venv/bin/python tests/test_cors.py
.venv/bin/python tests/verify_phase1.py
.venv/bin/python tests/verify_hash_table.py
```

`test_cors.py` uses Docker or Podman for its container checks and expects the
`scanly:test` image. The HTTP suites may need a fresh server between runs because
they exercise the rate limit.

## Limitations and scope

The browser app provides one image per scan and a direct JPEG download. It does
not have a document library, multi-page scans, accounts, authentication, cloud
sync, OCR, AI/ML, live edge tracking, confidence percentages, automatic capture,
or document-specific modes. Detection may fall back to the full image when no
page is found; the user can adjust the corners manually. Camera use depends on
browser support, permission, and HTTPS (except trusted localhost contexts).
