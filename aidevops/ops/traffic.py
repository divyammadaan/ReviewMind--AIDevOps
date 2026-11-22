"""Simulated shoppers: a gentle, variable stream of storefront traffic, switched on and off from the dashboard.

Why: with nobody using the shop the charts are flat and a canary release sees no real traffic. This module
makes the system look and behave as if people were using it, without being able to overload it:

  * the rate swells and falls on its own (a slow wave, a faster wobble, random jitter and short bursts)
    between about 1 and 20 requests per second (TRAFFIC_MAX, default 20), never more;
  * at most 8 requests are in flight at once, each with a 4 second timeout, and a request that cannot start
    straight away is dropped rather than queued, so a slow service never builds up a backlog;
  * if the shop gets slow or mostly fails, the rate drops to the minimum until it recovers;
  * every request goes through the gateway like a real browser would, so traces, metrics, logs and the
    canary's traffic split all see it.

Each simulated shopper browses, sometimes fills a cart and now and then checks out. Checkouts only use
products 2-6: product 1 is what the health probes measure, and touching its stock would cause false alarms.
"""
import math
import os
import random
import threading
import time
from collections import deque

import httpx

GW = "http://gateway:8080/api"
INVENTORY = "http://inventory:9102"
MAX_RPS = float(os.getenv("TRAFFIC_MAX", "20"))   # hard ceiling, requests per second
MIN_RPS = max(0.3, MAX_RPS * 0.05)
MAX_IN_FLIGHT = 8
MAX_SHOPPERS = 40
TIMEOUT = 4.0
SAFE_PRODUCTS = [2, 3, 4, 5, 6]


class Shopper:
    def __init__(self):
        self.user = f"sim-{random.randrange(10 ** 6):06d}"
        self.busy = False
        u = self.user
        steps = [("GET", "/catalog/products", None)]
        for pid in random.sample(range(1, 7), random.randint(1, 3)):
            steps.append(("GET", f"/catalog/products/{pid}", None))
        if random.random() < 0.6:
            for _ in range(random.randint(1, 2)):
                steps.append(("POST", f"/cart/cart/{u}/add", {"product_id": random.choice(SAFE_PRODUCTS), "qty": random.randint(1, 2)}))
            steps.append(("GET", f"/cart/cart/{u}", None))
            if random.random() < 0.25:
                steps.append(("POST", f"/orders/checkout/{u}", None))
            steps.append(("DELETE", f"/cart/cart/{u}", None))
        self.steps = deque(steps)


class Traffic:
    """`state` is the small dict shown on the dashboard: enabled, rps, sent, errors."""

    def __init__(self, state: dict, log):
        self.s = state
        self.log = log
        self.http = httpx.Client(timeout=TIMEOUT, limits=httpx.Limits(max_connections=MAX_IN_FLIGHT + 1))
        self.slots = threading.Semaphore(MAX_IN_FLIGHT)
        self.shoppers: list[Shopper] = []
        self.results: deque = deque(maxlen=30)   # (ok, milliseconds)
        self.burst_until = 0.0
        self.last_restock = 0.0
        self.was_on = False

    # ------------------------------------------------------------------------------------------ the rate
    def struggling(self):
        if len(self.results) < 10:
            return False
        fails = sum(1 for ok, _ in self.results if not ok)
        slow = sorted(ms for _, ms in self.results)[len(self.results) // 2] > 1500
        return fails / len(self.results) > 0.8 or slow

    def rate(self, now):
        m = MAX_RPS
        base = m * (0.42 + 0.28 * math.sin(now / 45 * 2 * math.pi) + 0.1 * math.sin(now / 13 * 2 * math.pi) + random.uniform(-0.04, 0.04))
        if now < self.burst_until:
            base *= 1.5
        elif random.random() < 0.004:           # roughly one burst every minute or so
            self.burst_until = now + random.uniform(4, 8)
        if self.struggling():
            base = MIN_RPS
        return max(MIN_RPS, min(MAX_RPS, base))

    # ---------------------------------------------------------------------------------------- one request
    def _send(self, shopper: Shopper):
        try:
            method, path, body = shopper.steps.popleft()
            t0 = time.time()
            try:
                r = self.http.request(method, GW + path, json=body)
                ok = r.status_code < 500          # 4xx (sold out, empty cart...) is normal shop behaviour
            except httpx.HTTPError:
                ok = False
            self.results.append((ok, (time.time() - t0) * 1000))
            self.s["sent"] += 1
            if not ok:
                self.s["errors"] += 1
        except IndexError:
            pass
        finally:
            shopper.busy = False
            if not shopper.steps:
                try:
                    self.shoppers.remove(shopper)
                except ValueError:
                    pass
            self.slots.release()

    def _launch(self):
        if not self.slots.acquire(blocking=False):
            return                                 # at the in-flight limit: drop this one, never queue
        idle = [s for s in self.shoppers if not s.busy]
        if idle and (len(self.shoppers) >= MAX_SHOPPERS or random.random() < 0.7):
            shopper = random.choice(idle)
        elif len(self.shoppers) < MAX_SHOPPERS:
            shopper = Shopper()
            self.shoppers.append(shopper)
        else:
            self.slots.release()
            return
        shopper.busy = True
        threading.Thread(target=self._send, args=(shopper,), daemon=True).start()

    def _restock(self):
        """The warehouse tops up products 2-6 now and then, so simulated checkouts never sell the shop out."""
        try:
            for pid in SAFE_PRODUCTS:
                left = self.http.get(f"{INVENTORY}/stock/{pid}").json()["in_stock"]
                if left < 40:
                    self.http.post(f"{INVENTORY}/restock", json={"product_id": pid, "qty": 80})
        except Exception:
            pass

    # ------------------------------------------------------------------------------------------ the loop
    def run(self):
        last, credit = time.time(), 0.0
        while True:
            time.sleep(0.2)
            now = time.time()
            dt, last = now - last, now
            on = bool(self.s["enabled"])
            if on != self.was_on:
                self.was_on = on
                self.log("info", "Simulated traffic started: a few shoppers browsing, filling carts and checking out." if on
                         else "Simulated traffic stopped.")
                self.shoppers.clear()
                self.results.clear()
                credit = 0.0
            if not on:
                self.s["rps"] = 0.0
                continue
            rps = self.rate(now)
            self.s["rps"] = round(rps, 1)
            credit += rps * dt
            while credit >= 1:
                credit -= 1
                self._launch()
            credit = min(credit, 1.0)
            if now - self.last_restock > 5:
                self.last_restock = now
                threading.Thread(target=self._restock, daemon=True).start()
