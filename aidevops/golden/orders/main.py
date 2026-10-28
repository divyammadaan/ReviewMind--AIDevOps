"""Orders service: turns a cart into a paid order."""
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="orders")

CART = "http://cart:9103"
INVENTORY = "http://inventory:9102"
PAYMENTS = "http://payments:9104"
ORDERS: dict[str, list] = {}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/checkout/{user}")
def checkout(user: str):
    cart = httpx.get(f"{CART}/cart/{user}", timeout=3).json()
    if not cart["items"]:
        raise HTTPException(400, "cart is empty")
    for item in cart["items"]:
        r = httpx.post(f"{INVENTORY}/reserve",
                       json={"product_id": item["product_id"], "qty": item["qty"]}, timeout=3)
        if r.status_code != 200:
            raise HTTPException(409, f"out of stock: {item['name']}")
    pay = httpx.post(f"{PAYMENTS}/charge", json={"user": user, "amount": cart["total"]}, timeout=3)
    if pay.status_code != 200 or pay.json()["status"] != "approved":
        raise HTTPException(402, "payment failed")
    order = {"order_id": f"ord_{sum(len(v) for v in ORDERS.values()) + 1001}", "user": user,
             "items": cart["items"], "total": cart["total"], "status": "confirmed"}
    ORDERS.setdefault(user, []).append(order)
    httpx.delete(f"{CART}/cart/{user}", timeout=3)
    return order


@app.get("/orders/{user}")
def history(user: str):
    return ORDERS.get(user, [])
