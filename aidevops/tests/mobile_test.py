"""End-to-end tests of the phone app, push notifications and the watchdog against the real stack.

    python tests/mobile_test.py

A small local server plays the part of a phone's push service. The test acts as the phone's browser: it
creates a real subscription keypair, receives the control plane's real encrypted Web Push messages, decrypts
them (inside the control-plane container, which has the decryption library) and verifies the VAPID signature
that identifies the sender. Needs Groq keys in ../.env for the watchdog (the LLM writes the fixes).
Faults are injected with report=false so no GitHub pull requests are opened.
"""
import base64
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from common import ENGINE, OPS, break_service, check, events, finish, has_discount_fix, post, reset_all, svc, wait

RECEIVED = []  # (path, lower-cased headers, body) of every push the control plane sent
PORT = 9998
HOST = os.getenv("PUSH_HOST", "host.docker.internal")  # how a container reaches this machine (Docker)


class PushService(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        RECEIVED.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(410 if self.path.startswith("/push/gone") else 201)  # 410: the phone unsubscribed
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


# On Docker the stand-in push service runs on this machine. On Podman (a Linux VM) Windows Firewall blocks the
# VM from reaching this machine, so it runs INSIDE the control-plane container instead (the push code under test
# is the same). Force either with PUSH_SINK=host|inside.
INSIDE = os.getenv("PUSH_SINK", "inside" if ENGINE == "podman" else "host") == "inside"
SINK = r"""
import base64, json, http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        with open("/tmp/push_sink.jsonl", "a") as f:
            f.write(json.dumps({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                "body": base64.b64encode(body).decode()}) + chr(10))
        self.send_response(410 if self.path.startswith("/push/gone") else 201)
        self.send_header("content-length", "0"); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 9998), H).serve_forever()
"""
if INSIDE:
    subprocess.run([ENGINE, "exec", "aidevops-ops", "sh", "-c", "rm -f /tmp/push_sink.jsonl; pkill -f push_sink 2>/dev/null; true"], capture_output=True)
    subprocess.Popen([ENGINE, "exec", "aidevops-ops", "python", "-c", "# push_sink" + chr(10) + SINK], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
else:
    threading.Thread(target=HTTPServer(("0.0.0.0", PORT), PushService).serve_forever, daemon=True).start()


def received():
    """Every push the control plane has sent so far, as (path, lower-cased headers, body)."""
    if not INSIDE:
        return list(RECEIVED)
    out = subprocess.run([ENGINE, "exec", "aidevops-ops", "sh", "-c", "cat /tmp/push_sink.jsonl 2>/dev/null"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    return [(r["path"], r["headers"], base64.b64decode(r["body"])) for r in (json.loads(l) for l in out.splitlines() if l.strip())]

b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
unb64u = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

# the "phone": a real P-256 keypair and auth secret, exactly what a browser creates for a push subscription
phone_key = ec.generate_private_key(ec.SECP256R1())
phone_pub = phone_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
phone_auth = os.urandom(16)
BASE = f"http://127.0.0.1:{PORT}" if INSIDE else f"http://{HOST}:{PORT}"
PHONE = {"endpoint": f"{BASE}/push/phone1", "keys": {"p256dh": b64u(phone_pub), "auth": b64u(phone_auth)}}
GONE = {"endpoint": f"{BASE}/push/gone", "keys": PHONE["keys"]}

DECRYPT = """
import sys, json, base64, http_ece
from cryptography.hazmat.primitives import serialization
d = json.load(sys.stdin)
key = serialization.load_pem_private_key(d["pem"].encode(), None)
print(http_ece.decrypt(base64.b64decode(d["body"]), private_key=key, auth_secret=base64.b64decode(d["auth"]), version="aes128gcm").decode())
"""


def decrypt(body: bytes) -> dict:
    pem = phone_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    out = subprocess.run([ENGINE, "exec", "-i", "aidevops-ops", "python", "-c", DECRYPT], capture_output=True, text=True,
                         input=json.dumps({"pem": pem, "body": base64.b64encode(body).decode(), "auth": base64.b64encode(phone_auth).decode()}))
    return json.loads(out.stdout)


def pushes(kind=None):
    out = []
    for path, headers, body in received():
        if path == "/push/phone1":
            msg = decrypt(body)
            if kind is None or msg["kind"] == kind:
                out.append((headers, msg))
    return out


def settings(**params):
    return httpx.post(f"{OPS}/api/settings", params=params, timeout=10).json()


info = httpx.get(f"{OPS}/api/mobile/info", timeout=10).json()
TOKEN = info["token"]
H = {"Authorization": f"Bearer {TOKEN}"}
mget = lambda path, **kw: httpx.get(f"{OPS}{path}", headers=H, timeout=15, **kw)
mpost = lambda path, **kw: httpx.post(f"{OPS}{path}", headers=H, timeout=15, **kw)

check(reset_all(), "stack healthy before testing")
mpost("/m/api/push/clear")  # forget phones left over from earlier runs
check(mget("/m/api/status").status_code == 200 and httpx.get(f"{OPS}/api/mobile/info", timeout=10).json()["subscribers"] == 0,
      "no phones are subscribed at the start (clean slate)")
settings(watchdog="false", agentic="false", canary_enabled="false")  # agents have their own test; this one saves tokens

# ---------------------------------------------------------------------------------------------
print("\n== 1. The phone app is served, and its API needs the token")
check(httpx.get(f"{OPS}/m/api/status", timeout=10).status_code == 401, "no token: refused")
check(httpx.get(f"{OPS}/m/api/status", headers={"Authorization": "Bearer wrong"}, timeout=10).status_code == 401, "wrong token: refused")
check(httpx.post(f"{OPS}/m/api/approve/cart", timeout=10).status_code == 401, "approving without the token is refused")
r = mget("/m/api/status")
check(r.status_code == 200 and {s["name"] for s in r.json()["services"]} >= {"cart", "orders"}, "right token: status for every service")
app = httpx.get(f"{OPS}/m/", timeout=10)
check(app.status_code == 200 and "serviceWorker" in app.text, "the app page is served (public: the token is typed into it)")
man = httpx.get(f"{OPS}/m/manifest.json", timeout=10).json()
check(man["display"] == "standalone" and {i["sizes"] for i in man["icons"]} == {"192x192", "512x512"}, "the manifest makes it installable")
check(all(httpx.get(f"{OPS}/m/{i['src']}", timeout=10).headers["content-type"] == "image/png" for i in man["icons"]), "both icons are served as PNG")
sw = httpx.get(f"{OPS}/m/sw.js", timeout=10)
check("showNotification" in sw.text and "notificationclick" in sw.text, "the service worker shows notifications and handles taps")
total_kb = sum(len(httpx.get(f"{OPS}/m/{f}", timeout=10).content) for f in ("", "sw.js", "manifest.json", "icon-192.png", "icon-512.png")) / 1024
check(total_kb < 60, f"the whole phone app is small ({total_kb:.0f} KB)")
check(mpost("/m/api/push/subscribe", json={"nope": 1}).status_code == 400, "a malformed push subscription is rejected")

# ---------------------------------------------------------------------------------------------
print("\n== 2. A real push notification: encrypted, signed, delivered, decrypted")
key = mget("/m/api/push/key").json()["public_key"]
check(key == info["public_key"] and len(unb64u(key)) == 65, "the server's VAPID public key is a valid uncompressed P-256 point")
check(mpost("/m/api/push/subscribe", json=PHONE).status_code == 200 and mpost("/m/api/push/subscribe", json=GONE).status_code == 200,
      "two phones subscribe (one will later say it has unsubscribed)")
check(break_service("cart", "cart-discount"), "cart broken (discount applied as 1000%)")
check(wait(lambda: any(p == "/push/phone1" for p, _, _ in received()), 30, every=1), "a push message arrives at the phone's push service")
headers, msg = pushes()[0]
check(headers.get("content-encoding") == "aes128gcm" and int(headers.get("ttl", 0)) > 0, "it is encrypted (aes128gcm) with a time-to-live")
check(msg["kind"] == "incident" and "Cart" in msg["title"] and msg["svc"] == "cart" and msg["url"] == "/m/#cart",
      "the phone decrypts it: 'Cart is broken', opening the app on that service", str(msg))
auth_header = headers.get("authorization", "")
check(auth_header.startswith("vapid ") and "t=" in auth_header and f"k={key}" in auth_header, "it carries a VAPID signature naming our public key")
jwt = auth_header.split("t=")[1].split(",")[0]
head, claims, sig = jwt.split(".")
try:
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(key)).verify(
        encode_dss_signature(int.from_bytes(unb64u(sig)[:32], "big"), int.from_bytes(unb64u(sig)[32:], "big")),
        f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    signature_ok = True
except InvalidSignature:
    signature_ok = False
c = json.loads(unb64u(claims))
check(signature_ok, "the VAPID signature verifies against the public key")
check(c["aud"] == BASE and c["exp"] > time.time() and c["sub"].startswith("mailto:"),
      "its claims name the push service, expire in the future and give a contact", str(c))
check(wait(lambda: httpx.get(f"{OPS}/api/mobile/info", timeout=5).json()["subscribers"] == 1, 15), "the phone that answered 410 is removed automatically")
hist = mget("/m/api/notifications").json()["notifications"]
check(any(n["kind"] == "incident" and n["svc"] == "cart" for n in hist), "the same alert appears in the app's alert list")

# ---------------------------------------------------------------------------------------------
print("\n== 3. The watchdog prepares fixes by itself, but a human still approves")
time.sleep(14)
check(svc("cart")["repair"]["state"] == "idle", "watchdog OFF: a failing service is left alone")
settings(watchdog="true")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 120), "watchdog ON: a fix is prepared with nobody clicking anything")
check(any("Watchdog" in e["msg"] for e in events()), "the activity log says the watchdog did it")
check(svc("cart")["status"] in ("broken", "down"), "nothing was applied: the service is still broken, awaiting approval")
check(wait(lambda: len(pushes("approval")) >= 1, 20), "the phone is notified that a fix is ready")
msg = pushes("approval")[0][1]
check("Cart" in msg["title"] and "approve" in msg["body"].lower(), "the notification says what to do", str(msg))
inc = mget("/m/api/incident/cart").json()
check(inc["repair"] == "pending_approval" and has_discount_fix(inc["proposal"]["diff"]), "the app can show the proposed diff for review")

print("   rejecting from the phone")
check(mpost("/m/api/reject/cart").status_code == 200 and svc("cart")["repair"]["state"] == "rejected", "Reject from the phone works")
time.sleep(14)
check(svc("cart")["repair"]["state"] == "rejected", "the watchdog does not keep re-proposing after a rejection")

# ---------------------------------------------------------------------------------------------
print("\n== 4. Approve from the phone")
post("/api/services/cart/fix", mode="golden")
wait(lambda: svc("cart")["status"] == "healthy", 40)
check(break_service("cart", "cart-discount"), "cart broken again")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 120), "watchdog prepares another fix")
before = len(pushes("resolved"))
check(mpost("/m/api/approve/cart").status_code == 200, "Approve & apply from the phone accepted")
check(wait(lambda: svc("cart")["repair"]["state"] == "resolved", 60), "the patch is applied and verified")
check(svc("cart")["status"] == "healthy" and svc("cart")["bug"] is None, "service healthy, fault cleared")
check(wait(lambda: len(pushes("resolved")) > before, 20), "the phone is told it is healthy again")
check(any("Approved from the phone app" in e["msg"] for e in events()), "the log records that the approval came from the phone")
check(mget("/m/api/status").json()["pending"] == [], "nothing is left waiting for approval")

mpost("/m/api/push/unsubscribe", json={"endpoint": PHONE["endpoint"]})
settings(watchdog="false", agentic="true")
reset_all()
finish()
