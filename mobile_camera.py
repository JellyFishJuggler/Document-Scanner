"""
mobile_camera.py
Phone camera -> OpenCV, via adb (USB debugging ya Wireless debugging).

Phone side (ek baar):
  1. Play Store se "IP Webcam" (Pavel Khlebovich) install karo.
  2. App kholo -> neeche scroll karo -> "Start server". App open rehne do.

PC side:
  USB       : phone plug karo, USB debugging "Allow" karo
              python mobile_camera.py
  Wireless  : Developer options -> Wireless debugging -> "Pair device with pairing code"
              adb pair <ip>:<pair_port>          (ek baar, code daalo)
              python mobile_camera.py --connect <ip>:<connect_port>

Apne scanner mein use karne ke liye:
    from mobile_camera import open_mobile_camera
    camera = open_mobile_camera()
"""
import argparse
import shutil
import subprocess
import sys

import cv2

PORT = 8080  # IP Webcam ka default port


def adb(*args):
    return subprocess.run(["adb", *args], capture_output=True, text=True)


def connect_device(connect_addr=None, port=PORT):
    if shutil.which("adb") is None:
        sys.exit("adb nahi mila. Android platform-tools install karo aur PATH mein add karo.")

    if connect_addr:  # wireless debugging
        print(adb("connect", connect_addr).stdout.strip())

    lines = adb("devices").stdout.strip().splitlines()[1:]
    devices = []
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            devices.append(parts[0])

    if not devices:
        sys.exit("Koi device nahi mila. USB/Wireless debugging on hai? Phone pe 'Allow' kiya?")

    serial = devices[0]
    # PC ka localhost:PORT -> phone ka PORT (USB aur wireless dono mein kaam karta hai)
    adb("-s", serial, "forward", f"tcp:{port}", f"tcp:{port}")
    return serial


def open_mobile_camera(connect_addr=None, port=PORT, url=None):
    """Returns a cv2.VideoCapture reading from the phone camera."""
    if url is None:
        serial = connect_device(connect_addr, port)
        print(f"Connected: {serial}")
        url = f"http://127.0.0.1:{port}/video"

    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # lag kam karne ke liye

    if not cap.isOpened():
        sys.exit("Stream nahi khuli. IP Webcam mein 'Start server' dabaya?")
    return cap


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", help="wireless debugging address, e.g. 192.168.1.5:41234")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--url", help="adb skip karke seedha stream URL use karo")
    args = ap.parse_args()

    cap = open_mobile_camera(args.connect, args.port, args.url)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imshow("Phone camera", frame)
        if cv2.waitKey(1) & 0xFF == 27:  # Esc
            break

    cap.release()
    cv2.destroyAllWindows()