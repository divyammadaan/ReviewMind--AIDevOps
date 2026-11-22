"""Inventory service: how many of each product are in stock."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="inventory")

STOCK = {1: 40, 2: 25, 3: 60, 4: 30, 5: 80, 6: 45}


class Change(BaseModel):
    product_id: int
    qty: int


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/stock/{pid}")
def stock(pid: int):
    if pid not in STOCK:
        raise HTTPException(404, "unknown product")
    return {"product_id": pid, "in_stock": STOCK[pid]}


@app.post("/reserve")
def reserve(r: Change):
    if r.qty <= 0:
        raise HTTPException(400, "qty must be positive")
    if r.qty > STOCK.get(r.product_id, 0):
        raise HTTPException(409, "insufficient stock")
    STOCK[r.product_id] -= r.qty
    return {"product_id": r.product_id, "reserved": r.qty, "remaining": STOCK[r.product_id]}


@app.post("/restock")
def restock(r: Change):
    STOCK[r.product_id] = STOCK.get(r.product_id, 0) + r.qty
    return {"product_id": r.product_id, "in_stock": STOCK[r.product_id]}
