"""Catalog service: the list of products the shop sells."""
from fastapi import FastAPI, HTTPException

app = FastAPI(title="catalog")

PRODUCTS = [
    {"id": 1, "name": "Wireless Headphones", "price": 29.99, "category": "Audio", "image": "/img/1.jpg"},
    {"id": 2, "name": "Mechanical Keyboard", "price": 4499, "category": "Desk", "image": "/img/2.jpg"},
    {"id": 3, "name": "Wireless Mouse", "price": 1799, "category": "Accessories", "image": "/img/3.jpg"},
    {"id": 4, "name": "Monitor Arm", "price": 2499, "category": "Desk", "image": "/img/4.jpg"},
    {"id": 5, "name": "Desk Lamp", "price": 1299, "category": "Desk", "image": "/img/5.jpg"},
    {"id": 6, "name": "Laptop Stand", "price": 1999, "category": "Desk", "image": "/img/6.jpg"},
]
BY_ID = {p["id"]: p for p in PRODUCTS}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/products")
def list_products():
    return PRODUCTS


@app.get("/products/{pid}")
def get_product(pid: int):
    product = BY_ID.get(pid)
    if product is None:
        raise HTTPException(404, "no such product")
    return product
