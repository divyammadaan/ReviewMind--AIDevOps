"""Payments service: approves or declines a charge."""
import itertools

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="payments")

_ids = itertools.count(1)


class Charge(BaseModel):
    user: str
    amount: float


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/charge")
def charge(c: Charge):
    if c.amount <= 0:
        raise HTTPException(400, "amount must be positive")
    if c.amount > 100000:
        raise HTTPException(402, "declined: over limit")
    return {"status": "approved", "txn_id": f"txn_{next(_ids):06d}", "amount": c.amount}
