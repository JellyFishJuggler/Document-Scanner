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
    
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl, tr, br, bl = pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]

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