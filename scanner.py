"""
scanner.py
Document scanning ke pure functions - koi camera, UI ya file-write dependency nahi.

Do jagah use hota hai:
  1. main.py  - local camera tool (import karke live loop chalata hai)
  2. api.py   - cloud HTTP server

Isiliye yahan kahin bhi cv2.imshow / imwrite / VideoCapture nahi hai.
Poora kaam memory mein hota hai.
"""

import cv2
import numpy as np


def order_corners(pts):
    """
    Chaar corners ko TL/TR/BR/BL mein order karo - chahe client ne bheja ho
    jis bhi order mein.

    s = x + y sabse chota 'top left', sabse bada 'bottom right'
    d = x - y sabse chota 'top right', sabse bada 'bottom left'

    Client ke liye ye zaroori hai: Adjust Corners screen par user chaaro nodes
    kisi bhi order mein drag kar sakta hai, aur Perspective transform ko corners
    ka sequence chahiye warna page ulta-palata warp ho jaata hai.
    """
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]


def four_point_transform(img, pts):

    '''
    used to transform the image into a flattened img  :)
    sets the points in order kyuki camera doesn't know ki kaunsa corner kaunsa h 
    
    s = x + y -> sabse chota 'top left' and 'bottom right' (diagonals)
    d = x - y -> sbase chota 'top right' and 'bottom left' (diagonals)
    
    distance calculate krni padhti hai fir taki actual document size mil paye aur uske basis pe flatten/cropping ho from complete camera view.
    
    src - acts the actual points of the document/image captured
    dst - acts as the label provided joh ek fixed rectangle shape banayega jisme src fit hoga
    
    getPerspectiveTransform - yeh src ko fit krwata h dst mein
    wrapPerspective - upr wale function se shirf matrix form return hoti h uss matrix ko image me fitting karta h yeh
    '''
        

    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    
    tl, tr, br, bl = order_corners(pts)

    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))

    src = np.float32([tl, tr, br, bl])
    dst = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, matrix, (width, height))


def resize_to_fit(img, max_w=800, max_h=600):
    '''
    Sirf display ke liye - preview, debug, screenshot.

    Scan banane ke liye mat use karo: binarize se pehle chhota karoge to text
    blur ho jaayega aur OCR bakwaas dega. scan_image() full resolution rakhta hai.
    '''
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    return cv2.resize(img, None, fx=scale, fy=scale)


def scan_detection(img, scale=0.4, return_fallback=False):
    
    
    '''    
    as the name suggests - iska kaam h ki document dhundh ke uske 4 points find karna 
    yaha contours ka use hota h kyuki this functionality helps in detecting the boundary of any object captured.
    
    filters - gray -> grayscale karke noise ko visible krwata h ; blur -> chote chote noise ko remove krne ke kaam aata h 
    
    otsu -> kaam ata h ki kaunsa part paper ka white hoga aur uska black (general case)
   
    kernel ka use - yeh 2 kaam ke use aata h for example agr light ka reflection h uski wjgh se joh noise ara hoga usko ignore krta h aur paper itself mein agr kahi kahi dots/dark spots h toh unko fill krta h 
    
    convexHull. Shape ko ek tight "elastic band" mein lapet deta hai, jisse andar ke dents bhar jaate hain.
    
    approxPolyDP. Outline ko kam corners wale polygon mein simplify karta hai. eps * peri tolerance hai (perimeter ka hissa). Chhota eps = zyada corners, bada eps = kam. Loop strict se dheela hota hai jab tak exactly 4 corners na mile.    
    
    approx / scale. Detection 0.4x pe hua tha, to coordinates ko wapas original size mein badalte hain. Warp full-quality image pe hoga.    
    
    return_fallback - default False (contour hi return karta hai, main.py ke backwards
    compatible hai). True pe (contour, used_fallback) ka tuple milta hai - taaki pata
    chale asli document mila ya poore frame ka fallback use hua. Server ko yeh chahiye.
    '''
    
    h, w = img.shape[:2]


    doc_contour = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.int32)

    small = cv2.resize(img, None, fx=scale, fy=scale)
    sh, sw = small.shape[:2]

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, threshold = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = np.ones((9, 9), np.uint8)

    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel)

    threshold = cv2.morphologyEx(threshold, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:3]:
        area = cv2.contourArea(contour)
        if not (0.1 * sh * sw < area < 0.9 * sh * sw):  # logo / poora frame ignore
            continue

        hull = cv2.convexHull(contour)  
        peri = cv2.arcLength(hull, True)
        for eps in (0.015, 0.02, 0.03, 0.04, 0.05):  
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4:
                found = (approx / scale).astype(np.int32)  # original size ke coordinates
                return (found, False) if return_fallback else found

    # fallback pehle resize se PEHLE banaya gaya hai (upar h, w original size ke),
    # isiliye ye coordinates original image pe theek hain.
    return (doc_contour, True) if return_fallback else doc_contour


def binarize(img, method="otsu"):
    '''
    Warped page ko black & white scan bana deta hai.

    method="otsu"     - default, poori image ka ek global threshold. Saaf, evenly
                        lit page pe best. Yahi main.py ka current behaviour hai.
    method="adaptive" - har area ka apna threshold (blockSize 31, C 10). Page ke
                        aadha ka andhera ho to yeh better hai. Opt-in only.
    '''
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if method == "otsu":
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    if method == "adaptive":
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            10,
        )

    raise ValueError(f"unknown method: {method!r} (use 'otsu' ya 'adaptive')")


def scan_image(img, max_side=None, method="otsu"):
    """
    Poora scan ek call mein: detect -> warp -> binarize.

    max_side - input image ke long side ko is size tak chhota karta hai, taaki CPU
               bounded rahe. Warped page hamesha apni poori resolution mein banta
               hai - humne jaan-boojh kar warp ke baad resize nahi kiya, warna
               chhota hone se text blur hota hai aur OCR fail hota hai.

    return dict: {"scan": ndarray, "contour": ndarray, "used_fallback": bool}
    """
    if max_side:
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            factor = max_side / max(h, w)
            img = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)

    contour, used_fallback = scan_detection(img, return_fallback=True)

    warped = four_point_transform(img, contour.reshape(4, 2))
    scan = binarize(warped, method=method)

    return {
        "scan": scan,
        "contour": contour,
        "used_fallback": used_fallback,
    }


# ---------------------------------------------------------------- corners

def validate_corners(corners, tolerance=0.02):
    """
    Client ke bheje corners validate karo. ValueError raise hoti hai - API ise
    422 me convert karti hai.

    Rules (spec ke mutabiq):
      - exactly 4 points, har ek [x, y]
      - normalized [0, 1] ke andar. `tolerance` chhoti si galti maarta hai
        (0.02 = 2000px image par 40px) - kyunki client ke normalized coords
        round-trip mein kabhi-kabar 1.001 ya -0.001 ho jaate hain
      - convex - concave quadrilateral perspective warp nahi deta
      - area >= 5% of the frame - 4% wala crop matlab user ne galti se
        almost kuch nahi select kiya

    Returns the corners ordered TL/TR/BR/BL, dtype float32.
    """
    try:
        pts = np.asarray(corners, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"corners ko numbers ki list chahiye: {exc}") from exc

    if pts.shape != (4, 2):
        raise ValueError(f"corners me exactly 4 [x, y] points hone chahiye, mila shape {pts.shape}")

    if not np.isfinite(pts).all():
        raise ValueError("corners me NaN ya Infinity nahi ho sakta")

    if pts.min() < -tolerance or pts.max() > 1 + tolerance:
        raise ValueError(
            f"corners normalized 0..1 ke andar hone chahiye, mila "
            f"min={pts.min():.3f} max={pts.max():.3f}"
        )

    pts = np.clip(pts, 0.0, 1.0)

    ordered = np.array(order_corners(pts), dtype=np.float32)

    # Area canonical order me ginna zaroori hai. cv2.contourArea order-sensitive
    # hai - bowtie (self-intersecting) order me ek bilkul valid quad ka area ~0
    # aata hai aur wo 422 ho jaata. Client corners kisi bhi order me bhejta
    # hai (Adjust Corners screen par drag sequence fixed nahi), isiliye area
    # check order ke baad hi karna padta hai.
    area = cv2.contourArea(ordered)
    if area < 0.05:
        raise ValueError(f"corners ka area {area:.1%} hai, kam se kam 5% chahiye")

    # float32 me check karo, int32 me nahi. Corners normalized 0..1 hain - int32
    # me cast karte hi sab 0 ya 1 ho jaate hain aur har valid quad "non-convex"
    # report hota. Yeh shape (4, 2) bhi zaroori hai: OpenCV contour ko ya to
    # Nx1x2 ya 1xNx2 chahta hai, Nx2 par shape mismatch error aata hai.
    if not cv2.isContourConvex(ordered.reshape(-1, 1, 2)):
        raise ValueError("corners convex quadrilateral nahi hain (ek corner bahar ya andar hai)")

    return ordered


def scale_corners(corners, width, height):
    """
    Normalized corners ko pixel coordinates me badal do.

    `np.array(..., copy=True)` zaroori hai. `np.asarray` already-float32 array par
    wahi object wapas deta hai, aur phir `pts[:, 0] *= width` CALLER ke array ko
    in-place badal deta hai. Isse do bugs aate hain: (a) request ke dauran
    `ordered_norm` pixels me badal jaata hai, to response ka `corners` field
    0.25 bhejne ke bajaye 400.0 bhejta hai; (b) dobara scale karne par values
    1600x ho jaati hain aur clip ke sarf 1599/1199 ho jaati hain.
    """
    pts = np.array(corners, dtype=np.float32, copy=True).reshape(4, 2)
    pts[:, 0] *= width
    pts[:, 1] *= height
    return np.clip(pts, 0, [width - 1, height - 1])


def prepare_page(img, max_side=None, corners=None):
    """
    Page ko warp karne ke liye taiyaar karo. Detection aur manual-corners dono
    ke liye ek hi code path.

    corners None  -> automatically detect karo (pehle wala behaviour)
    corners given -> unhi par warp karo, detection bilkul skip

    Returns dict:
        page        - COLOR warped page (ndarray) - Preview screen isi ko dikhata hai
        contour     - pixel coordinates jo use hue (4, 2)
        detected    - True agar automatic detection ne page dhoondha
        corners_norm- wahi 4 corners normalized TL/TR/BR/BL form me, source image
                      ke hisaab se - client ke paas bhejne ke liye
        scale       - resize factor jo `page` par laga (client ko pata chalega
                      ki warped page source resolution se chhota ho sakta hai)
    """
    h, w = img.shape[:2]
    scale = 1.0

    work = img
    if max_side and max(h, w) > max_side:
        scale = max_side / max(h, w)
        work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if corners is None:
        contour, used_fallback = scan_detection(work, return_fallback=True)
        contour_px = np.asarray(contour, dtype=np.float32).reshape(4, 2)
        detected = not used_fallback
        # contour `work` (resized) par hai. Client ko SOURCE frame ke normalized
        # corners chahiye, isliye pehle source pixels me wapas lao, phir 0..1
        # me scale karo. Sirf /scale karna kaafi nahi tha - wo source pixels
        # deta hai, normalized nahi.
        contour_src = contour_px / scale
        corners_norm = contour_src / np.array([w, h], dtype=np.float32)
    else:
        contour_px = scale_corners(corners, work.shape[1], work.shape[0])
        # Client ne bheje hue corners pehle se normalized hain, aur validation
        # ne 0..1 + convex + area guarantee kar diya hai. Order yahin set hota hai.
        corners_norm = np.array(order_corners(np.asarray(corners, dtype=np.float32).reshape(4, 2)),
                                dtype=np.float32)
        detected = True

    page = four_point_transform(work, contour_px)

    return {
        "page": page,
        "contour": contour_px,
        "detected": detected,
        "corners_norm": corners_norm,
        "scale": scale,
    }


# ---------------------------------------------------------------- enhancement

def estimate_background(img):
    """
    Page ka background (paper) estimate karo - i.e. page ka wo roshni map jo
    upar se neeche ya ek side se dusri side pe badalta hai.

    Trick: `cv2.morphologyEx(..., MORPH_CLOSE)` ek bade kernel ke saath. Ye
    bright regions ko expand karta hai aur dark regions ko fill karta hai, to
    text/lines khatam ho jaate hain aur jo bacha wahi paper ka illumination hai.

    Kernel size ko image ka ek fraction rakhna zaroori hai - fix 31px chhoti
    image par poora paper ko "background" bana dega aur koi correction nahi hogi.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = max(15, (min(gray.shape[:2]) // 12) | 1)  # odd lena zaroori
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)


def remove_shadow(img):
    """
    Uneven lighting hatao - division method.

    har pixel ko apne local background se divide karo, phir 255 se scale karo.
    Divide karne se paper ke andar ka contrast bacha rehta hai, sirf roshni ka
    variation flat ho jaata hai.

    Ye "crease elimination" nahi hai - ek sharp diagonal fold jo lighting nahi,
    geometry hai, isse poori tarah nahi mita. Claim bhi nahi kiya jaata.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = estimate_background(img)
    # divide karne se pehle background ko float me le jao - uint8 me zero
    # division par silent zero aa jaata hai.
    bg_f = bg.astype(np.float32) + 1.0
    flat = np.clip(gray.astype(np.float32) * 255.0 / bg_f, 0, 255).astype(np.uint8)
    return cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)


def enhance(img, preset="original", shadow_removal=False):
    """
    Warped page par ek filter apply karo.

    Presets:
      original      - color, as-is (bas optional shadow removal)
      grayscale     - single channel gray
      bw            - Otsu, sirf 0 aur 255
      bw_adaptive   - adaptive threshold, uneven page par better
      clean_white   - background normalize karke paper ko safed, text dark

    shadow_removal - upar wala desk-shadow removal, kisi bhi preset ke saath.

    Return dict: {"image": ndarray, "channels": int, "timing_ms": dict}
    `channels` 1 hoga grayscale/bw pe, 3 baaki me - client ko encoding ke liye
    chahiye.

    `timing_ms` me `shadow_ms` aur `preset_ms` alag hain, warna client ko
    pata hi nahi chalega ki 250ms me se 219ms background estimate me gaya
    ya actual filter me.
    """
    import time as _time

    t0 = _time.perf_counter()
    if shadow_removal:
        work = remove_shadow(img)
    else:
        work = img
    t1 = _time.perf_counter()

    if preset == "original":
        out = work
    elif preset == "grayscale":
        out = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    elif preset == "bw":
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif preset == "bw_adaptive":
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        out = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )
    elif preset == "clean_white":
        # Ye preset khud apna lighting flat karta hai (background estimate ke
        # liye bada morphological kernel), isiliye `shadow_removal=False` par
        # bhi ye step chalta hai. Agar client ne `shadow_removal=True` bheja
        # tha to upar wala `work` already flat hai - dobara estimate karna
        # quantization error laata hai bina kuch sudhare.
        flat = work if shadow_removal else remove_shadow(img)
        gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
        lo, hi = np.percentile(gray, (2, 98))
        if hi - lo < 1:  # khaali/flat page - stretch mat karo
            out = gray
        else:
            stretched = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
            out = np.clip((stretched.astype(np.float32) - 8) * 1.06, 0, 255).astype(np.uint8)
    else:
        raise ValueError(
            f"unknown preset: {preset!r} (use 'original', 'grayscale', 'bw', "
            f"'bw_adaptive' ya 'clean_white')"
        )
    t2 = _time.perf_counter()

    channels = 1 if out.ndim == 2 else out.shape[2]
    return {
        "image": out,
        "channels": channels,
        "timing_ms": {
            "shadow": (t1 - t0) * 1000,
            "preset": (t2 - t1) * 1000,
            # clean_white apna background estimation preset stage me karta hai,
            # isiliye uska `preset` time naturally bada hota hai.
        },
    }