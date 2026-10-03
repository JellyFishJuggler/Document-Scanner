import os

# unncessary font warnings ko hide krne ke liye 
if os.path.isdir("/usr/share/fonts/truetype/dejavu"):
    os.environ["QT_QPA_FONTDIR"] = "/usr/share/fonts/truetype/dejavu"

import cv2
import numpy as np
from mobile_camera import open_mobile_camera

camera = open_mobile_camera()


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
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    return cv2.resize(img, None, fx=scale, fy=scale)


def scanDetection(img, scale=0.4):
    
    
    '''    
    as the name suggests - iska kaam h ki document dhundh ke uske 4 points find karna 
    yaha contours ka use hota h kyuki this functionality helps in detecting the boundary of any object captured.
    
    filters - gray -> grayscale karke noise ko visible krwata h ; blur -> chote chote noise ko remove krne ke kaam aata h 
    
    otsu -> kaam ata h ki kaunsa part paper ka white hoga aur uska black (general case)
   
    kernel ka use - yeh 2 kaam ke use aata h for example agr light ka reflection h uski wjgh se joh noise ara hoga usko ignore krta h aur paper itself mein agr kahi kahi dots/dark spots h toh unko fill krta h 
    
    convexHull. Shape ko ek tight "elastic band" mein lapet deta hai, jisse andar ke dents bhar jaate hain.
    
    approxPolyDP. Outline ko kam corners wale polygon mein simplify karta hai. eps * peri tolerance hai (perimeter ka hissa). Chhota eps = zyada corners, bada eps = kam. Loop strict se dheela hota hai jab tak exactly 4 corners na mile.    
    
    approx / scale. Detection 0.4x pe hua tha, to coordinates ko wapas original size mein badalte hain. Warp full-quality image pe hoga.    
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
                return (approx / scale).astype(np.int32)  # original size ke coordinates

    return doc_contour


while True:

    success, frame = camera.read()
    if not success:
        break

    doc_contour = scanDetection(frame)

    view = frame.copy()
    cv2.drawContours(view, [doc_contour], -1, (0, 255, 0), 3)
    cv2.imshow('Camera', resize_to_fit(view))

    wrapped = four_point_transform(frame, doc_contour.reshape(4, 2))
    wrapped = resize_to_fit(wrapped)
    # cv2.imshow('Wrapped', resize_to_fit(wrapped))
    
    gray = cv2.cvtColor(wrapped, cv2.COLOR_BGR2GRAY)

    scanned = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    cv2.imshow('Scanned', resize_to_fit(scanned))
    
    key = cv2.waitKey(1) & 0xFF

    if key == ord('s'):
        print(" 's' key pressed! Saving image...")
        cv2.imwrite('output/captured_frame.png', scanned)

    elif key == ord('q'):
        print("'q' key pressed! Exiting...")
        break

camera.release()
cv2.destroyAllWindows()