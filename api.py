"""
api.py
Document scanner ka HTTP API. scanner.py ka logic yahan se expose hota hai.

Sirf memory mein kaam karta hai - koi bhi uploaded image disk pe likhi nahi jaati,
koi image data log nahi hota. Response mein wahi B/W scan wapas jaata hai.

Run (local):
    .venv/bin/python api.py

Endpoints:
    GET  /health   -> liveness (Render ke health check ke liye)
    POST /scan     -> {"image": "<base64 JPEG>", "corners": [[x,y] x4]?}
    POST /enhance  -> {"image": "<base64 JPEG>", "preset": ..., "output": ...}

No authentication, no accounts. Scanly is a single-user local/mobile app with no
user identity to authenticate, so an API key would only be a shared secret baked
into the shipped app bundle. The protections that still matter without auth are
all kept: body cap, format allow-list, pixel limit, rate limit, concurrency cap.
"""

import hashlib
import os

# ---------------------------------------------------------------- pixel limit
#
# Ye do kaam karta hai, dono zaroori hain:
#
# 1. MAX_DECODED_PIXELS - apna limit (default 50M pixels). 50M ka BGR array
#    150MB hota hai, jo 512MB wale free instance ke andar sambha jayega.
#
# 2. OpenCV ka apna limit. Uska default ~1e9 pixels hai - matlab 4e8 pixels wala
#    image (12GB BGR!) default roop se ACCEPT ho jaata hai aur OpenCV allocate
#    karne lagta hai. Maine 20000x20000 wala ek chhota file banakar test kiya:
#    bina env var ke wo "Insufficient memory" diya (mere test ne rlimit maara),
#    env var ke saath saaf reject hua. Ye env var cv2 import se PEHLE set hona
#    zaroori hai, kyunki OpenCV library init par ise padhta hai - isliye ye
#    `import cv2` se pehle rakha hai.
#
# Saath hi neeche `read_image_size()` header padh ke bhi khud check karta hai, taaki
# (a) allocation hone se pehle hi pakda jaaye, (b) error message saaf aaye.
# OpenCV ka assertion sirf ek cv2.error deta hai jo message mein kuch nahi batata.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", os.environ.get("MAX_DECODED_PIXELS", "50000000"))

import base64
import binascii
import logging
import threading
import time
from typing import Literal

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from scanner import binarize, enhance, prepare_page, validate_corners

# ---------------------------------------------------------------- config

# No API key. Scanly has no accounts and no user identity, so there is nothing to
# authenticate - only one user's own app talking to its own backend. A shared
# secret here would have to live inside the mobile bundle, which makes it
# public (unzip the APK). Abuse is bounded by RATE_LIMIT_PER_MIN, the body cap,
# the pixel limit and MAX_CONCURRENT_SCANS instead of by identity.
MAX_BODY_BYTES = 12 * 1024 * 1024  # 12 MB - base64 overhead ke liye generous
MAX_DECODED_PIXELS = int(os.environ.get("MAX_DECODED_PIXELS", "50000000"))
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
MAX_CONCURRENT_SCANS = int(os.environ.get("MAX_CONCURRENT_SCANS", "2"))

# CORS. Explicit origins - wildcard kabhi nahi.
#
# `ALLOWED_ORIGINS` comma-separated list hai. Default localhost:5173 (Vite dev
# server ka port). Wildcard `*` isliye nahi, kyunki:
#   1. isse koi bhi website browser ke zariye hamare API ko call kar sakti hai
#   2. browser `Access-Control-Allow-Credentials: true` ke saath `*` ko reject
#      karta hai, to wildcard cookies ke liye bhi kaam nahi karta
# No browser credentials are used, so allow_credentials=False is appropriate.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
if not ALLOWED_ORIGINS:
    raise SystemExit("ALLOWED_ORIGINS khaali nahi ho sakta - CORS ke liye kam se kam ek origin chahiye")
if "*" in ALLOWED_ORIGINS:
    raise SystemExit(
        "ALLOWED_ORIGINS me '*' allowed nahi hai - koi bhi website browser se "
        "API call kar sakegi. Explicit origins likho."
    )

Method = Literal["otsu", "adaptive"]
Preset = Literal["original", "grayscale", "bw", "bw_adaptive", "clean_white"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scanner.api")

# OpenCV / numpy thread-safe hain, par ek free-tier instance pe CPU chhoti hai.
# Isiliye ek do scan se zyada ek saath queue mein nahi jaate, warna request timeouts
# milte hain instead of a clean 429/503.
_scan_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SCANS)

# ---------------------------------------------------------------- rate limit

_WINDOW_SECONDS = 60.0
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def enforce_rate_limit(bucket_id: str) -> None:
    now = time.monotonic()
    with _hits_lock:
        times = [t for t in _hits.get(bucket_id, []) if now - t < _WINDOW_SECONDS]
        if len(times) >= RATE_LIMIT_PER_MIN:
            retry_after = _WINDOW_SECONDS - (now - times[0])
            raise HTTPException(
                status_code=429,
                detail=f"rate limit {RATE_LIMIT_PER_MIN}/min cross ho gaya, {retry_after:.0f}s baad try karo",
            )
        times.append(now)
        _hits[bucket_id] = times

        # dict ko unbounded grow karne se roka
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
                _hits.pop(k, None)


def client_identity(request: Request) -> str:
    """
    Rate-limit bucket key, without authenticating anybody.

    Uses the socket peer address (`request.client.host`) and deliberately does
    NOT read `X-Forwarded-For`, because a client can set that header to anything
    and would then get a fresh rate-limit bucket per request - i.e. no limit at
    all. The trade-off is real and worth stating: behind a reverse proxy (Render)
    every request arrives from the same proxy IP, so this collapses to ONE shared
    bucket for the whole instance. For a single-user app that is the correct
    behaviour - it caps total CPU burn on the box. If this were ever multi-tenant,
    the fix is to trust the proxy's forwarded header only for known proxy IPs.
    """
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode("utf-8")).hexdigest()[:32]


async def read_body_capped(request: Request, cap: int) -> tuple[bytes, bool]:
    """
    Body padho, par cap cross hone ke baad buffer karna chhodh do - baaki data
    discard karte jao.

    Yeh isliye zaroori hai: agar hum cap cross karke turant 413 bhej dein bina
    body khatam kiye, to server beech mein connection band kar deta hai aur client
    ko "Connection reset" milta hai - wo apna 413 padh hi nahi paata. Poora data
    drain karne se client ko saaf 413 milta hai, aur memory bhi bounded rehti hai
    kyunki hum bytes ko store nahi karte.

    After the cap is crossed, data is discarded while the request is drained;
    memory remains bounded and the client receives a clean 413 response.
    """
    buf = bytearray()
    overflow = False
    async for chunk in request.stream():
        if overflow:
            continue  # drain karte raho, store nahi karte
        buf.extend(chunk)
        if len(buf) > cap:
            overflow = True
            buf.clear()
    return bytes(buf), overflow


# ---------------------------------------------------------------- image size

# JPEG ke "SOF" markers - inme hi width/height hoti hai. SOF4 (DHT), SOF8 (JPG) aur
# SOF12 (DAC) me dimensions nahi hote, isliye wo chhodne padenge.
_JPEG_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
# Marker jiske aage koi length field nahi hota.
_JPEG_STANDALONE = {0x01, 0xD8, 0xD9} | set(range(0xD0, 0xD8))


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """JPEG ke header se (width, height) - bina pixel decode kiye."""
    n = len(data)
    if n < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None  # SOI nahi hai

    i = 2
    while i + 1 < n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1  # fill byte
            continue
        if marker in _JPEG_STANDALONE:
            i += 2
            continue
        if marker == 0xDA:
            return None  # SOS se image data shuru, matlab SOF mila hi nahi
        if i + 3 >= n:
            return None
        seglen = (data[i + 2] << 8) | data[i + 3]
        if seglen < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if i + 9 > n:
                return None
            height = (data[i + 5] << 8) | data[i + 6]
            width = (data[i + 7] << 8) | data[i + 8]
            return width, height
        i += 2 + seglen
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    """PNG ke IHDR chunk se (width, height)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def read_image_size(data: bytes) -> tuple[int, int] | None:
    """
    Header se dimensions nikaalo - koi allocation nahi, koi decode nahi.

    Ye zaroori hai kyunki 12MB ka body cap akela kaafi nahi: ek solid-colour image
    12MB se bhi choti ho sakti hai par 40000x40000 = 1.6 BILLION pixels hote hain,
    jiske liye BGR array 4.8GB lega. File chhoti, bomb badi.
    """
    return _jpeg_size(data) or _png_size(data)


_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ALLOWED_FORMATS = {_JPEG_MAGIC: "JPEG", _PNG_MAGIC: "PNG"}


def sniff_format(data: bytes) -> str | None:
    """
    Magic bytes se format pehchaano. Sirf JPEG aur PNG allow hain.

    Phone camera JPEG bhejta hai. Baaki formats (WebP/BMP/TIFF/PPM) reject karne
    se ye guarantee milti hai ki har request ka header hum parse kar sakte hain -
    unke liye pixel-limit sirf OPENCV_IO_MAX_IMAGE_PIXELS par tiki hoti, jo ek
    import-order par latka hua single point of failure hai.
    """
    for magic, name in _ALLOWED_FORMATS.items():
        if data.startswith(magic):
            return name
    return None


async def decode_and_guard(b64_image: str) -> tuple[np.ndarray, dict]:
    """
    base64 -> bytes -> ndarray, saare guardrails ke saath. /scan aur /enhance dono
    isko call karte hain - ek jagah se. Iska matlab security checks drift nahi kar
    sakte: naya endpoint add karte waqt guardrails bhoolna structurally impossible
    hai, kyunki dono endpoints literally yahi function call karte hain.

    Order (Phase A me yahi order tai hua tha, wahi hai):
      base64 -> khaali? -> magic bytes allow-list -> header pixel limit ->
      decode -> post-decode pixel verify

    Returns (decoded_bgr, timings) jahan timings me b64_decode aur decode ms hain.
    """
    # base64 -> bytes. IMREAD_COLOR default EXIF orientation apply karta hai,
    # isliye phone ki 6/8 wali rotated photos bhi seedha upright padhti hain.
    t_b64 = time.perf_counter()
    try:
        image_bytes = base64.b64decode(b64_image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image base64 valid nahi hai") from exc
    b64_ms = (time.perf_counter() - t_b64) * 1000

    if not image_bytes:
        raise HTTPException(status_code=400, detail="image khaali hai")

    # format allow-list. Magic bytes decode se pehle - taaki unknown format
    # ka bada buffer kabhi decode attempt hi na ho.
    if sniff_format(image_bytes) is None:
        raise HTTPException(status_code=415, detail="sirf JPEG ya PNG supported hai")

    # pixel limit - header se, decode se PEHLE. Yahan koi allocation nahi hoti,
    # to chhoti file + bade dimensions wala bomb yahin ruk jaata hai.
    size = read_image_size(image_bytes)
    if size is not None:
        hdr_w, hdr_h = size
        if hdr_w <= 0 or hdr_h <= 0:
            raise HTTPException(status_code=400, detail="image header mein dimensions invalid hain")
        pixels = hdr_w * hdr_h
        if pixels > MAX_DECODED_PIXELS:
            log.warning(
                "rejected by pixel limit: %dx%d = %.1fM pixels (limit %.1fM, file %d bytes)",
                hdr_w, hdr_h, pixels / 1e6, MAX_DECODED_PIXELS / 1e6, len(image_bytes),
            )
            raise HTTPException(
                status_code=413,
                detail=(
                    f"image bohot badi hai: {hdr_w}x{hdr_h} = {pixels / 1e6:.1f}M pixels, "
                    f"limit {MAX_DECODED_PIXELS / 1e6:.0f}M. Chhoti photo bhejein."
                ),
            )

    t_decode = time.perf_counter()
    try:
        decoded = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    except MemoryError as exc:
        raise HTTPException(status_code=503, detail="server busy - dobara try karo") from exc
    except cv2.error as exc:
        # Corrupt/truncated JPEG bhi cv2.error deta hai. 413 sirf tabhi jab OpenCV
        # ne khud pixel-limit assert kiya ho; baaki sab 400 (galat image) hain.
        if not _is_pixel_limit_error(exc):
            raise HTTPException(
                status_code=400,
                detail="image decode nahi hui (corrupt ya invalid JPEG/PNG)",
            ) from exc
        raise HTTPException(
            status_code=413,
            detail="image bohot badi hai - dimensions limit se zyada hain",
        ) from exc

    if decoded is None:
        raise HTTPException(status_code=400, detail="image decode nahi hui (corrupt ya unsupported format)")

    # defense in depth: header kehta hi nahi tha (ya jhooth bola) to ab verify karo
    src_h, src_w = decoded.shape[:2]
    if src_h * src_w > MAX_DECODED_PIXELS:
        log.warning(
            "rejected after decode: %dx%d = %.1fM pixels (limit %.1fM)",
            src_w, src_h, src_h * src_w / 1e6, MAX_DECODED_PIXELS / 1e6,
        )
        raise HTTPException(status_code=413, detail=f"image {src_w}x{src_h} pixel limit cross karti hai")

    return decoded, {
        "b64_decode": round(b64_ms, 2),
        "decode": round((time.perf_counter() - t_decode) * 1000, 2),
    }


def _is_pixel_limit_error(exc: cv2.error) -> bool:
    """
    Har cv2.error pixel-limit nahi hota - corrupt data, truncated file, invalid
    colour model, ye sab bhi cv2.error dete hain. 413 sirf usi error ke liye hai
    jisme OpenCV ne khud pixel-limit assert kiya ho, warna galat image hai (400).
    """
    msg = str(exc)
    return "CV_IO_MAX_IMAGE_PIXELS" in msg or "validateInputImageSize" in msg


# ---------------------------------------------------------------- app

app = FastAPI(
    title="Scanly API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

# CORS middleware. `expose_headers` taaki browser JS response headers padh sake.
# Koi custom header allow nahi karte - React Native `fetch` CORS par depend nahi
# karta; ye un future browser-based debug tools ke liye hai.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)


class ScanRequest(BaseModel):
    image: str
    method: Method = "otsu"
    max_side: int | None = 2000
    # Adjust Corners screen par user ne jo corners drag kiye. Normalized [0, 1],
    # chaaron ka order koi bhi ho sakta hai - server TL/TR/BR/BL me order karega.
    # None = automatic detection chalao (default, pehle wala behaviour).
    corners: list[list[float]] | None = None


class EnhanceRequest(BaseModel):
    image: str
    preset: Preset = "original"
    shadow_removal: bool = False
    # Output format. JPEG chhota hai (photos ke liye behtar), PNG lossless hai.
    # Default JPEG quality 90 ke saath - document photos me visually lossless
    # ke qareeb, par aadhi se bhi kam bytes.
    #
    # Naam `output` hai, `output_format` nahi - yahi agreed Phase B contract
    # hai. `output_format` purana spelling hai; koi purana client na tootey,
    # isliye woh deprecated alias ke roop me bhi accept hota hai. Dono ek saath
    # bheje jaane par `output` jeetta hai.
    output: Literal["jpeg", "png"] = "jpeg"
    output_format: Literal["jpeg", "png"] | None = None
    quality: int | None = None

    @model_validator(mode="after")
    def _resolve_output(self) -> "EnhanceRequest":
        if self.output_format is not None:
            object.__setattr__(self, "_deprecated_used", True)
            if self.output_format != self.output and self.output != "jpeg":
                # Dono alat-alat aur `output` explicitly non-default hai to
                # unclear hai - client ki galti batayein.
                raise ValueError(
                    "output aur output_format dono alag-alag diye gaye hain; "
                    "sirf 'output' use karo (output_format deprecated hai)"
                )
            object.__setattr__(self, "output", self.output_format)
        return self


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "scanly",
        "version": "1.0.0",
        "method": "classical computer vision",
        "cv2": cv2.__version__,
        "numpy": np.__version__,
        "max_decoded_pixels": MAX_DECODED_PIXELS,
        "allowed_origins": ALLOWED_ORIGINS,
    }


@app.post("/scan")
async def scan(request: Request) -> JSONResponse:
    t_start = time.perf_counter()

    # 1. rate limit. Ab koi identity authenticate nahi hoti, to bucket key
    #    socket peer address se banti hai (client_identity). Ye ek security
    #    feature nahi, ek capacity brake hai - isse ek runaway loop ya abusive
    #    client poore CPU nahi khaa sakta.
    enforce_rate_limit(client_identity(request))

    # 2. body (bounded + drained, taaki oversized upload ko saaf 413 mile)
    raw, overflow = await read_body_capped(request, MAX_BODY_BYTES)
    if overflow:
        raise HTTPException(status_code=413, detail="image 12MB se badi hai")
    t_body_done = time.perf_counter()

    # 4. parse
    try:
        payload = ScanRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"bad request body: {exc.errors()[0]['msg']}") from exc

    if payload.max_side is not None and not (200 <= payload.max_side <= 6000):
        raise HTTPException(status_code=422, detail="max_side 200 se 6000 ke beech hona chahiye")

    # 5. corners validate karo (agar bheje gaye hain). ValueError -> 422.
    #    Yahan decode se pehle, kyunki ye decision client ke input ka hai.
    ordered_norm = None
    if payload.corners is not None:
        try:
            ordered_norm = validate_corners(payload.corners)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    decoded, timings = await decode_and_guard(payload.image)

    src_h, src_w = decoded.shape[:2]

    # 7. warp + binarize (CPU heavy - threadpool mein, bounded concurrency ke saath).
    #    `corners` None hua to automatic detection chalti hai; warna unhi
    #    client-supplied corners par warp hota hai aur detection skip ho jaati hai.
    if not _scan_slots.acquire(timeout=30):
        raise HTTPException(status_code=503, detail="busy, thodi der baad try karo")

    try:
        t_scan = time.perf_counter()
        warped = await run_in_threadpool(
            prepare_page, decoded, payload.max_side, ordered_norm
        )
        scan_img = await run_in_threadpool(binarize, warped["page"], payload.method)
        scan_ms = (time.perf_counter() - t_scan) * 1000

        t_enc = time.perf_counter()
        encoded = await run_in_threadpool(encode_png_base64, scan_img)
        warped_b64 = await run_in_threadpool(encode_jpeg_base64, warped["page"])
        encode_ms = (time.perf_counter() - t_enc) * 1000
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _scan_slots.release()

    warped_page = warped["page"]
    used_fallback = not warped["detected"] and ordered_norm is None

    timing = {
        "body_read": round((t_body_done - t_start) * 1000, 2),
        "b64_decode": timings["b64_decode"],
        "decode": timings["decode"],
        "scan": round(scan_ms, 2),
        "encode": round(encode_ms, 2),
        "total": round((time.perf_counter() - t_start) * 1000, 2),
    }

    # image data kabhi log nahi hota - sirf dimensions aur timings
    log.info(
        "scan ok method=%s in=%dx%d scan_out=%dx%d warped=%dx%d fallback=%s corners=%s ms=%s",
        payload.method,
        src_w,
        src_h,
        scan_img.shape[1],
        scan_img.shape[0],
        warped_page.shape[1],
        warped_page.shape[0],
        used_fallback,
        "client" if ordered_norm is not None else "detected",
        timing,
    )

    return JSONResponse(
        {
            "ok": True,
            # --- Phase A fields, unchanged ---
            "scan_b64": encoded,
            "scan_png_mime": "image/png",
            "used_fallback": used_fallback,
            "detected": warped["detected"],
            "contour": warped["contour"].reshape(4, 2).tolist(),
            "source_size": [src_w, src_h],
            "scan_size": [int(scan_img.shape[1]), int(scan_img.shape[0])],
            "method": payload.method,
            "timing_ms": timing,
            # --- Phase B additions ---
            # Corners normalized TL/TR/BR/BL me, source image ke hisaab se.
            "corners": warped["corners_norm"].tolist(),
            "corners_source": "client" if ordered_norm is not None else "detected",
            # Asli COLOR warped page - Preview screen ye dikhata hai, filters
            # isi par lagte hain. scan_b64 (B&W) ke alawa.
            "warped_b64": warped_b64,
            "warped_size": [int(warped_page.shape[1]), int(warped_page.shape[0])],
            "warped_mime": "image/jpeg",
        }
    )


@app.post("/enhance")
async def enhance_endpoint(request: Request) -> JSONResponse:
    """
    Warped page par ek filter. /scan ke wahi guardrails - shared helper se, to
    security surface dono me identical hai.

    Request:  {"image": "<b64>", "preset": "bw", "shadow_removal": false,
               "output": "jpeg"}
    Response: {"ok": true, "image_b64": ..., "mime": ..., "size": [w, h],
               "channels": 1|3, "preset": ..., "shadow_removal": ..., "timing_ms": {...}}
    """
    t_start = time.perf_counter()

    enforce_rate_limit(client_identity(request))

    raw, overflow = await read_body_capped(request, MAX_BODY_BYTES)
    if overflow:
        raise HTTPException(status_code=413, detail="image 12MB se badi hai")
    t_body_done = time.perf_counter()

    try:
        payload = EnhanceRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"bad request body: {exc.errors()[0]['msg']}") from exc

    if payload.quality is not None and not (10 <= payload.quality <= 100):
        raise HTTPException(status_code=422, detail="quality 10 se 100 ke beech hona chahiye")

    decoded, timings = await decode_and_guard(payload.image)
    src_h, src_w = decoded.shape[:2]

    if not _scan_slots.acquire(timeout=30):
        raise HTTPException(status_code=503, detail="busy, thodi der baad try karo")

    try:
        t_proc = time.perf_counter()
        result = await run_in_threadpool(
            enhance, decoded, preset=payload.preset, shadow_removal=payload.shadow_removal
        )
        proc_ms = (time.perf_counter() - t_proc) * 1000
        stage_ms = result["timing_ms"]

        t_enc = time.perf_counter()
        if payload.output == "png":
            b64, mime = await run_in_threadpool(encode_png_raw, result["image"])
        else:
            q = payload.quality if payload.quality is not None else 90
            b64, mime = await run_in_threadpool(
                encode_jpeg_raw, result["image"], q
            )
        encode_ms = (time.perf_counter() - t_enc) * 1000
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        _scan_slots.release()

    out = result["image"]
    # Stage timings alag-alag: `shadow` = background estimation,
    # `preset` = actual filter (clean_white me background estimation bhi
    # preset stage me girti hai, isiliye uska preset time bada hota hai).
    # `enhance` = dono ka total, Phase A style back-compat ke liye.
    timing = {
        "body_read": round((t_body_done - t_start) * 1000, 2),
        "b64_decode": timings["b64_decode"],
        "decode": timings["decode"],
        "shadow": round(stage_ms["shadow"], 2),
        "preset": round(stage_ms["preset"], 2),
        "enhance": round(proc_ms, 2),
        "encode": round(encode_ms, 2),
        "total": round((time.perf_counter() - t_start) * 1000, 2),
    }

    # Insaan padhne layak: sirf dimensions. Image data, filenames, EXIF - kuch nahi.
    log.info(
        "enhance ok preset=%s shadow=%s in=%dx%d out=%dx%d channels=%d ms=%s",
        payload.preset,
        payload.shadow_removal,
        src_w,
        src_h,
        out.shape[1],
        out.shape[0],
        result["channels"],
        timing,
    )

    return JSONResponse(
        {
            "ok": True,
            "image_b64": b64,
            "mime": mime,
            "size": [int(out.shape[1]), int(out.shape[0])],
            "channels": result["channels"],
            "preset": payload.preset,
            "shadow_removal": payload.shadow_removal,
            "output": payload.output,
            "source_size": [src_w, src_h],
            "timing_ms": timing,
        }
    )


def encode_png_base64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("scan ko PNG me encode nahi kar paya")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def encode_jpeg_base64(img: np.ndarray, quality: int = 90) -> str:
    return encode_jpeg_raw(img, quality)[0]


def encode_jpeg_raw(img: np.ndarray, quality: int = 90) -> tuple[str, str]:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("image ko JPEG me encode nahi kar paya")
    return base64.b64encode(buf.tobytes()).decode("ascii"), "image/jpeg"


def encode_png_raw(img: np.ndarray) -> tuple[str, str]:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("image ko PNG me encode nahi kar paya")
    return base64.b64encode(buf.tobytes()).decode("ascii"), "image/png"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,  # 1 taaki in-memory rate limit ek hi jagah rahe
        proxy_headers=False,  # never derive the rate-limit peer from client-supplied X-Forwarded-For
        log_level="info",
    )
