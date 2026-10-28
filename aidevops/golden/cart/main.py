"""Cart service: what each shopper has picked, priced from the catalog."""
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="cart")

CATALOG = "http://catalog:9101"
CARTS: dict[str, dict[int, int]] = {}


class Item(BaseModel):
    product_id: int
    qty: int = 1


def discount_for(total: float) -> float:
    """10% off orders of 5000 or more."""
    if total >= 5000:
        return round(total * 0.10, 2)
    return 0.0


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/cart/{user}/add")
def add(user: str, item: Item):
    if item.qty <= 0:
        raise HTTPException(400, "qty must be positive")
    found = httpx.get(f"{CATALOG}/products/{item.product_id}", timeout=3)
    if found.status_code != 200:
        raise HTTPException(404, "product not available")
    cart = CARTS.setdefault(user, {})
    cart[item.product_id] = cart.get(item.product_id, 0) + item.qty
    return {"ok": True}


@app.get("/cart/{user}")
def view(user: str):
    lines = []
    subtotal = 0
    for pid, qty in CARTS.get(user, {}).items():
        product = httpx.get(f"{CATALOG}/products/{pid}", timeout=3).json()
        line_total = product["cost"] * qty
        lines.append({"product_id": pid, "name": product["name"], "image": product["image"],
                      "price": product["price"], "qty": qty, "line_total": line_total})
        subtotal += line_total
    discount = discount_for(subtotal)
    return {"user": user, "items": lines, "subtotal": subtotal,
            "discount": discount, "total": round(subtotal - discount, 2)}


@app.delete("/cart/{user}")
def clear(user: str):
    CARTS.pop(user, None)
    return {"ok": True}
