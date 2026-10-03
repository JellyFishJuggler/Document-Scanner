"""
api.py
Document scanner ka HTTP API. scanner.py ka logic yahan se expose hota hai.

Sirf memory mein kaam karta hai - koi bhi uploaded image disk pe likhi nahi jaati,
koi image data log nahi hota. Response mein wahi B/W scan wapas jaata hai.

Run (local):
    API_KEY=kuch-secret .venv/bin/python -m uvicorn api:app --host 0.0.0.0 --port 8000

Endpoints:
    GET  /health   -> liveness, koi auth nahi (Render ke health check ke liye)
    POST /scan     -> {"image": "<base64 JPEG>"}   (X-API-Key header zaroori)
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
import secrets
import threading
import time
from typing import Literal

import cv2
import numpy as np
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from scanner import scan_image

# ---------------------------------------------------------------- config

# Fail closed: API_KEY set hi nahi hui to server start hi nahi hoga. Isse galti se
# public scanner expose hone se better hai. Render pe env var bhool jaane par deploy
# fail hoga, jo ki sahi behaviour hai.
API_KEY = os.environ.get("API_KEY", "").strip()
if not API_KEY:
    raise SystemExit(
        "API_KEY env var set nahi hai. Server start nahi hoga. "
        "Jaise: API_KEY=<apna-secret> python -m uvicorn api:app"
    )

MAX_BODY_BYTES = 12 * 1024 * 1024  # 12 MB - base64 overhead ke liye generous
MAX_DECODED_PIXELS = int(os.environ.get("MAX_DECODED_PIXELS", "50000000"))
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
MAX_CONCURRENT_SCANS = int(os.environ.get("MAX_CONCURRENT_SCANS", "2"))

Method = Literal["otsu", "adaptive"]

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


def enforce_rate_limit(key_id: str) -> None:
    now = time.monotonic()
    with _hits_lock:
        times = [t for t in _hits.get(key_id, []) if now - t < _WINDOW_SECONDS]
        if len(times) >= RATE_LIMIT_PER_MIN:
            retry_after = _WINDOW_SECONDS - (now - times[0])
            raise HTTPException(
                status_code=429,
                detail=f"rate limit {RATE_LIMIT_PER_MIN}/min cross ho gaya, {retry_after:.0f}s baad try karo",
            )
        times.append(now)
        _hits[key_id] = times

        # dict ko unbounded grow karne se roka
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
                _hits.pop(k, None)


def require_api_key(x_api_key: str | None) -> str:
    """
    API key verify karo aur uska ek opaque id wapas do.

    `secrets.compare_digest` literally wahi function hai jo `hmac.compare_digest`
    hai (stdlib mein alias - maine `is` se verify kiya: True, dono ka object
    `_hashlib.compare_digest` hai). Constant time hai, to timing se key guess
    nahi ki ja sakti.

    Wapas diya gaya id SHA-256 hai, raw key nahi - taaki ram mein secret ka plain
    text copy na ghoomte rahe. Yehi id rate limiting ke liye use hoti hai.
    """
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="X-API-Key header missing hai")
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="X-API-Key galat hai")
    return hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()


def require_api_key(x_api_key: str | None) -> None:
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="X-API-Key header missing hai")
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="X-API-Key galat hai")


async def read_body_capped(request: Request, cap: int) -> tuple[bytes, bool]:
    """
    Body padho, par cap cross hone ke baad buffer karna chhodh do - baaki data
    discard karte jao.

    Yeh isliye zaroori hai: agar hum cap cross karke turant 413 bhej dein bina
    body khatam kiye, to server beech mein connection band kar deta hai aur client
    ko "Connection reset" milta hai - wo apna 413 padh hi nahi paata. Poora data
    drain karne se client ko saaf 413 milta hai, aur memory bhi bounded rehti hai
    kyunki hum bytes ko store nahi karte.

    Note: yeh auth ke BAAD chalta hai, isliye unauthorized client ka data hum
    buffer nahi karte - wo connection reset hi paata hai. Yeh jaanbujhkar hai:
    security pehle. Legit app (valid key) ko saaf 413 milega.
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


class ScanRequest(BaseModel):
    image: str
    method: Method = "otsu"
    max_side: int | None = 2000


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
    }


@app.post("/scan")
async def scan(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> JSONResponse:
    t_start = time.perf_counter()

    # 1. API key. Iska opaque id rate-limit key banega - IP nahi. Hum Render ke proxy
    #    ke peeche hain, aur X-Forwarded-For client khud bhar sakta hai, to uspar
    #    bharosa nahi kiya jaata. API key wo cheez hai jo genuinely pehchanti hai.
    key_id = require_api_key(x_api_key)

    # 2. rate limit (API key ke hisaab se)
    enforce_rate_limit(key_id)

    # 3. body (bounded + drained, taaki oversized upload ko saaf 413 mile)
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

    # 5. base64 -> bytes -> image. IMREAD_COLOR default EXIF orientation apply karta
    #    hai, isliye phone ki 6/8 wali rotated photos bhi seedha upright padhti hain.
    t_b64 = time.perf_counter()
    try:
        image_bytes = base64.b64decode(payload.image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image base64 valid nahi hai") from exc
    b64_ms = (time.perf_counter() - t_b64) * 1000

    if not image_bytes:
        raise HTTPException(status_code=400, detail="image khaali hai")

    # 6. format allow-list. Magic bytes decode se pehle - taaki unknown format
    #    ka bada buffer kabhi decode attempt hi na ho.
    if sniff_format(image_bytes) is None:
        raise HTTPException(
            status_code=415,
            detail="sirf JPEG ya PNG supported hai",
        )

    # 7. pixel limit - header se, decode se PEHLE. Yahan koi allocation nahi hoti,
    #    to chhoti file + bade dimensions wala bomb yahin ruk jaata hai.
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
    decode_ms = (time.perf_counter() - t_decode) * 1000

    # 7. scan (CPU heavy - threadpool mein, aur bounded concurrency ke saath)
    if not _scan_slots.acquire(timeout=30):
        raise HTTPException(status_code=503, detail="busy, thodi der baad try karo")

    try:
        t_scan = time.perf_counter()
        result = await run_in_threadpool(
            scan_image, decoded, max_side=payload.max_side, method=payload.method
        )
        scan_ms = (time.perf_counter() - t_scan) * 1000

        t_enc = time.perf_counter()
        encoded = await run_in_threadpool(encode_png_base64, result["scan"])
        encode_ms = (time.perf_counter() - t_enc) * 1000
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _scan_slots.release()

    scan_img = result["scan"]
    used_fallback = result["used_fallback"]

    timing = {
        "body_read": round((t_body_done - t_start) * 1000, 2),
        "b64_decode": round(b64_ms, 2),
        "decode": round(decode_ms, 2),
        "scan": round(scan_ms, 2),
        "encode": round(encode_ms, 2),
        "total": round((time.perf_counter() - t_start) * 1000, 2),
    }

    # image data kabhi log nahi hota - sirf dimensions aur timings
    log.info(
        "scan ok method=%s in=%dx%d out=%dx%d fallback=%s ms=%s",
        payload.method,
        src_w,
        src_h,
        scan_img.shape[1],
        scan_img.shape[0],
        used_fallback,
        timing,
    )

    return JSONResponse(
        {
            "ok": True,
            "scan_b64": encoded,
            "scan_png_mime": "image/png",
            "used_fallback": used_fallback,
            "detected": not used_fallback,
            "contour": result["contour"].reshape(4, 2).tolist(),
            "source_size": [src_w, src_h],
            "scan_size": [int(scan_img.shape[1]), int(scan_img.shape[0])],
            "method": payload.method,
            "timing_ms": timing,
        }
    )


def encode_png_base64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("scan ko PNG me encode nahi kar paya")
    return base64.b64encode(buf.tobytes()).decode("ascii")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,  # 1 taaki in-memory rate limit ek hi jagah rahe
        log_level="info",
    )