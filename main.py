import os

# unncessary font warnings ko hide krne ke liye 
if os.path.isdir("/usr/share/fonts/truetype/dejavu"):
    os.environ["QT_QPA_FONTDIR"] = "/usr/share/fonts/truetype/dejavu"

import cv2
from mobile_camera import open_mobile_camera
from scanner import four_point_transform, resize_to_fit, scan_detection

camera = open_mobile_camera()


while True:

    success, frame = camera.read()
    if not success:
        break

    doc_contour = scan_detection(frame)

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