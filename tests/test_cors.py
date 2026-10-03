"""Phase B - CORS.

`ALLOWED_ORIGINS` explicit honi chahiye, wildcard bilkul nahi. Ye test:
  - configured origin ko allow milta hai (Access-Control-Allow-Origin echo hota hai)
  - preflight OPTIONS sahi headers ke saath 200
  - EK BHI random origin allow nahi hota
  - '*' configure karne par server start hi nahi hota (fail closed)

Section 5 (server refuses to start on a wildcard) cannot be tested over HTTP -
the process has to boot and exit. It runs as a separate container start:

    podman run --rm -e API_KEY=x -e ALLOWED_ORIGINS='*' scanly:test
    -> exits non-zero with "ALLOWED_ORIGINS me '*' allowed nahi hai"

Verified separately; see the Phase B report.

Run:  SCANLY_TEST_URL=http://127.0.0.1:18001 .venv/bin/python tests/test_cors.py
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import _paths
from _paths import API, KEY, REPO

RESULTS = []
ALLOWED = "http://localhost:5173"
EVIL = "http://evil.example.com"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def headers_of(r):
    """HTTP headers, lowercased keys.

    `dict(r.headers)` looks right but is not: urllib's headers object IS
    case-insensitive, and copying it into a plain dict throws that away. The
    server sends `Access-Control-Allow-Origin`, the copy keys it as
    `access-control-allow-origin`, and every lookup returns None. Keeping the
    message object itself (or lowercasing) is what actually works.
    """
    return {k.lower(): v for k, v in r.headers.items()}


def get_on(base, path, origin=None, method="GET"):
    """Same as get() but against an arbitrary base URL."""
    req = urllib.request.Request(base + path, method=method)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, headers_of(r)
    except urllib.error.HTTPError as e:
        return e.code, headers_of(e)


def get(path, origin=None, method="GET"):
    req = urllib.request.Request(API + path, method=method)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, headers_of(r)
    except urllib.error.HTTPError as e:
        return e.code, headers_of(e)


def preflight(path, origin):
    req = urllib.request.Request(API + path, method="OPTIONS")
    req.add_header("Origin", origin)
    req.add_header("Access-Control-Request-Method", "POST")
    req.add_header("Access-Control-Request-Headers", "content-type,x-api-key")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, headers_of(r)
    except urllib.error.HTTPError as e:
        return e.code, headers_of(e)


print("=" * 74)
print("PHASE B - CORS")
print("=" * 74)

print()
print("1. /health advertises the configured origins")
req = urllib.request.Request(API + "/health")
with urllib.request.urlopen(req, timeout=30) as r:
    health = json.loads(r.read())
check("allowed_origins present in /health", "allowed_origins" in health)
check(f"'{ALLOWED}' is configured",
      ALLOWED in health.get("allowed_origins", []), str(health.get("allowed_origins")))
check("no wildcard in the configured list", "*" not in health.get("allowed_origins", []),
      str(health.get("allowed_origins")))

print()
print("2. simple request from the allowed origin")
st, h = get("/health", origin=ALLOWED)
check("status 200", st == 200, f"got {st}")
check("Access-Control-Allow-Origin echoes our origin",
      h.get("access-control-allow-origin") == ALLOWED,
      str(h.get("access-control-allow-origin")))
check("Vary: Origin is set (shared caches must not serve the wrong CORS header)",
      "origin" in (h.get("vary") or "").lower(), str(h.get("vary")))
check("credentials are NOT allowed", h.get("access-control-allow-credentials") is None,
      str(h.get("access-control-allow-credentials")))

print()
print("3. preflight from the allowed origin")
st, h = preflight("/scan", ALLOWED)
check("preflight 200", st == 200, f"got {st}")
check("allow-origin present", h.get("access-control-allow-origin") == ALLOWED,
      str(h.get("access-control-allow-origin")))
methods = (h.get("access-control-allow-methods") or "").upper()
check("POST is allowed", "POST" in methods, str(h.get("access-control-allow-methods")))
check("OPTIONS is allowed", "OPTIONS" in methods, str(h.get("access-control-allow-methods")))
hdrs = (h.get("access-control-allow-headers") or "").lower()
check("X-API-Key header is allowed", "x-api-key" in hdrs, str(h.get("access-control-allow-headers")))
check("Content-Type header is allowed", "content-type" in hdrs,
      str(h.get("access-control-allow-headers")))
check("max-age is set (so browsers do not re-preflight constantly)",
      (h.get("access-control-max-age") or "0") != "0", str(h.get("access-control-max-age")))

st, h = preflight("/enhance", ALLOWED)
check("preflight for /enhance -> 200 with allow-origin",
      st == 200 and h.get("access-control-allow-origin") == ALLOWED, f"got {st}")

print()
print("4. a DISALLOWED origin gets no CORS grant")
st, h = get("/health", origin=EVIL)
check("request still succeeds server-side (CORS is a browser policy)",
      st == 200, f"got {st}")
check("but NO Access-Control-Allow-Origin header",
      h.get("access-control-allow-origin") is None,
      str(h.get("access-control-allow-origin")))

st, h = preflight("/scan", EVIL)
check("preflight from an unknown origin is refused",
      h.get("access-control-allow-origin") is None,
      f"status {st}, header {h.get('Access-Control-Allow-Origin')}")

print()
print("5. server refuses to start with a wildcard (fail closed)")
# A wildcard would let any website send authenticated requests with our API key
# in the background. The server must refuse to boot rather than fall open.
proc = subprocess.run(
    ["podman", "run", "--rm", "-e", f"API_KEY={KEY}", "-e", "ALLOWED_ORIGINS=*",
     "scanly:test"],
    capture_output=True, text=True, timeout=180,
)
out = proc.stdout + proc.stderr
check("exits non-zero on ALLOWED_ORIGINS='*'", proc.returncode != 0,
      f"returncode {proc.returncode}")
check("explains why", "'*' allowed nahi hai" in out,
      [ln for ln in out.splitlines() if "allowed" in ln][:2])

proc2 = subprocess.run(
    ["podman", "run", "--rm", "-e", f"API_KEY={KEY}", "-e", "ALLOWED_ORIGINS= , ",
     "scanly:test"],
    capture_output=True, text=True, timeout=180,
)
out2 = proc2.stdout + proc2.stderr
check("exits non-zero when the list is all whitespace", proc2.returncode != 0,
      f"returncode {proc2.returncode}")
check("explains that at least one origin is required",
      "kam se kam ek origin" in out2,
      [ln for ln in out2.splitlines() if "origin" in ln][:2])

print()
print("6. a comma-separated list of origins works (not just a single value)")
PORT = 18002
multi = f"http://127.0.0.1:{PORT}"
NAME = "scanly-test-cors"


def free_port(port):
    """Remove anything still bound to this port.

    An earlier run of this test left an auto-named container holding the port
    (`podman run -d` without --name), so the next start fails with "Address
    already in use" while the assertions below happily query the STALE
    container and pass. That is a false green - the port must be provably ours.
    """
    out = subprocess.run(
        ["podman", "ps", "--format", "{{.Names}} {{.Ports}}"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    for line in out.splitlines():
        if f":{port}->" in line:
            subprocess.run(["podman", "rm", "-f", line.split()[0]],
                           capture_output=True)


free_port(PORT)
subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)

proc3 = subprocess.run(
    ["podman", "run", "--rm", "--name", NAME, "-e", f"API_KEY={KEY}",
     "-e", f"ALLOWED_ORIGINS={ALLOWED},https://app.scanly.app",
     "-p", f"{PORT}:8000", "-d", "scanly:test"],
    capture_output=True, text=True, timeout=180,
)
check("container with two origins started", proc3.returncode == 0,
      (proc3.stderr or proc3.stdout)[:150])

ready = False
if proc3.returncode == 0:
    for _ in range(20):
        try:
            with urllib.request.urlopen(multi + "/health", timeout=3) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(1)

if not ready:
    check("two-origin container came up", False, "never became healthy")
else:
    check("two-origin container came up", True)
    for origin in (ALLOWED, "https://app.scanly.app"):
        st, hh = get_on(multi, "/health", origin=origin)
        check(f"configured origin '{origin}' is allowed",
              hh.get("access-control-allow-origin") == origin,
              str(hh.get("access-control-allow-origin")))
    st, hh = get_on(multi, "/health", origin="https://not-configured.example")
    check("an unlisted origin is still refused on the multi-origin config",
          hh.get("access-control-allow-origin") is None,
          str(hh.get("access-control-allow-origin")))

subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)

print()
print("=" * 74)
failed = [n for n, ok in RESULTS if not ok]
print(f"TOTAL: {len(RESULTS)}   PASS {len(RESULTS) - len(failed)}   FAIL {len(failed)}")
if failed:
    print("FAILURES:")
    for n in failed:
        print(f"  - {n}")
sys.exit(1 if failed else 0)