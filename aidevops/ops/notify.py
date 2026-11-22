"""Notifications: an in-app history plus Web Push to subscribed phones.

Web Push (VAPID) needs no third-party app, account or app store: the phone's browser registers with its own
push service and this module sends encrypted messages to it. Keys and subscriptions are kept on the shared
volume so they survive restarts. The mobile token (which protects the phone API) lives here too.
"""
import base64
import json
import os
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

STATE = Path("/runtime/notify.json")
SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:aidevops@example.com")


class Notifier:
    def __init__(self):
        self.lock = threading.Lock()
        self.history: deque = deque(maxlen=100)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.sent = self.failed = 0
        self.last_error = ""
        self.state = self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self):
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if "vapid_pem" not in state:
            v = Vapid()
            v.generate_keys()
            state["vapid_pem"] = v.private_pem().decode()
        state.setdefault("subs", [])
        state.setdefault("token", secrets.token_urlsafe(18))
        if os.getenv("MOBILE_TOKEN", "").strip():
            state["token"] = os.getenv("MOBILE_TOKEN").strip()
        self._save(state)
        return state

    def _save(self, state=None):
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(state or self.state), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ identity
    @property
    def token(self):
        return self.state["token"]

    def public_key(self):
        """The VAPID public key in the form browsers need (URL-safe base64 of the uncompressed point)."""
        v = Vapid.from_pem(self.state["vapid_pem"].encode())
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    # --------------------------------------------------------------- subscriptions
    def subscribe(self, sub: dict):
        if not sub.get("endpoint") or "keys" not in sub:
            raise ValueError("not a push subscription")
        with self.lock:
            self.state["subs"] = [s for s in self.state["subs"] if s["endpoint"] != sub["endpoint"]] + [sub]
            self._save()

    def unsubscribe(self, endpoint: str):
        with self.lock:
            self.state["subs"] = [s for s in self.state["subs"] if s["endpoint"] != endpoint]
            self._save()

    def clear(self):
        with self.lock:
            self.state["subs"] = []
            self._save()

    @property
    def subscribers(self):
        return len(self.state["subs"])

    # ---------------------------------------------------------------------- sending
    def notify(self, kind, svc, title, body):
        rec = {"id": f"{int(time.time() * 1000):x}", "t": time.time(), "kind": kind, "svc": svc,
               "title": title, "body": body}
        with self.lock:
            self.history.appendleft(rec)
            subs = list(self.state["subs"])
        payload = json.dumps({"title": title, "body": body, "svc": svc, "kind": kind, "tag": f"{kind}-{svc}",
                              "url": f"/m/#{svc}" if svc else "/m/"})
        for sub in subs:
            self.pool.submit(self._push, sub, payload)
        return rec

    def _push(self, sub, payload):
        try:
            webpush(subscription_info=sub, data=payload, vapid_private_key=Vapid.from_pem(self.state["vapid_pem"].encode()),
                    vapid_claims={"sub": SUBJECT}, ttl=3600, timeout=6, headers={"Urgency": "high"})
            self.sent += 1
        except WebPushException as e:
            self.failed += 1
            code = getattr(e.response, "status_code", None)
            self.last_error = f"push service answered {code}" if code else "push failed"
            if code in (404, 410):  # the phone unsubscribed or the subscription expired
                self.unsubscribe(sub["endpoint"])
        except Exception as e:  # network down, bad endpoint...: notifications must never break the control plane
            self.failed += 1
            self.last_error = type(e).__name__

    def info(self):
        return {"subscribers": self.subscribers, "sent": self.sent, "failed": self.failed, "last_error": self.last_error}


notifier = Notifier()
