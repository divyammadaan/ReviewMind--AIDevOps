"""Checkout API for a small online store.

Run it:
    uvicorn service:app --port 9000 --reload

Then try:
    http://localhost:9000/customer/101
    http://localhost:9000/checkout/101?amount=1000
    http://localhost:9000/checkout/999?amount=1000
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import storefront
from customers import customer_tier, find_customer
from orders import order_total, savings
from pricing import apply_discount, discount_percent

app = FastAPI(title="Checkout API")


@app.get("/")
def index():
    return {
        "service": "Checkout API",
        "try": ["/customer/101", "/checkout/101?amount=1000", "/tiers"],
    }


@app.get("/tiers")
def tiers():
    """Show the discount available at each tier."""
    return {
        "gold": f"{discount_percent('gold')}% off",
        "silver": f"{discount_percent('silver')}% off",
        "regular": f"{discount_percent('regular')}% off",
    }


@app.get("/customer/{customer_id}")
def get_customer(customer_id: int):
    """Return a customer's record."""
    customer = find_customer(customer_id)
    if customer is None:
        return {"status": 404, "error": "no such customer"}
    return {"status": 200, "data": customer}


@app.get("/shop", response_class=HTMLResponse)
def shop(customer: int = 101, amount: float = 1000.0):
    """The customer-facing checkout page."""
    record = find_customer(customer)
    if record is None:
        return HTMLResponse(storefront.NOT_FOUND, status_code=200)

    tier = record["tier"]
    percent = discount_percent(tier)
    payable = apply_discount(amount, tier)
    saved = amount - payable

    return HTMLResponse(storefront.render(
        name=record["name"], tier=tier, discount=percent,
        base=amount, saved=saved, total=payable,
        expected=apply_discount(amount, tier),
    ))


@app.get("/checkout/{customer_id}")
def checkout(customer_id: int, amount: float = 1000.0):
    """Work out what this customer pays for an order."""
    return {
        "status": 200,
        "customer": customer_id,
        "tier": customer_tier(customer_id),
        "base_amount": amount,
        "you_save": savings(customer_id, amount),
        "total_payable": order_total(customer_id, amount),
    }

