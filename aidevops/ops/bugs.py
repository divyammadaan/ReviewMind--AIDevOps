"""Catalogue of injectable faults. Each one is a one-line source mutation.

The fixer never sees this file: it only gets the failing probe and the logs.
"""

BUGS = {
    "catalog": [
        {"id": "catalog-typo", "title": "Typo in product lookup",
         "old": "product = BY_ID.get(pid)", "new": "product = BY_IDS.get(pid)"},
        {"id": "catalog-price", "title": "Price stored in wrong unit",
         "old": '"price": 2999,', "new": '"price": 29.99,'},
        {"id": "catalog-truncate", "title": "Catalog list truncated",
         "old": "    return PRODUCTS\n", "new": "    return PRODUCTS[:2]\n"},
    ],
    "inventory": [
        {"id": "inventory-oversell", "title": "Overselling allowed",
         "old": "if r.qty > STOCK.get(r.product_id, 0):",
         "new": "if r.qty > STOCK.get(r.product_id, 0) + 1000000:"},
        {"id": "inventory-decrement", "title": "Stock decremented by wrong amount",
         "old": "STOCK[r.product_id] -= r.qty", "new": "STOCK[r.product_id] -= 1"},
        {"id": "inventory-keyerror", "title": "Stock lookup uses wrong key type",
         "old": '"in_stock": STOCK[pid]}', "new": '"in_stock": STOCK[str(pid)]}'},
    ],
    "cart": [
        {"id": "cart-discount", "title": "Discount applied as 1000%",
         "old": "return round(total * 0.10, 2)", "new": "return round(total * 10, 2)"},
        {"id": "cart-overwrite", "title": "Cart overwrites quantity instead of adding",
         "old": "cart[item.product_id] = cart.get(item.product_id, 0) + item.qty",
         "new": "cart[item.product_id] = item.qty"},
        {"id": "cart-keyerror", "title": "Wrong field read from catalog",
         "old": 'line_total = product["price"] * qty', "new": 'line_total = product["cost"] * qty'},
    ],
    "payments": [
        {"id": "payments-zero", "title": "Zero-amount charges accepted",
         "old": "if c.amount <= 0:", "new": "if c.amount < 0:"},
        {"id": "payments-pending", "title": "Charges never approved",
         "old": '"status": "approved"', "new": '"status": "pending"'},
        {"id": "payments-syntax", "title": "Syntax error (service will not boot)",
         "old": "def charge(c: Charge):", "new": "def charge(c: Charge)"},
    ],
    "orders": [
        {"id": "orders-port", "title": "Wrong payments port",
         "old": 'PAYMENTS = "http://payments:9104"', "new": 'PAYMENTS = "http://payments:9140"'},
        {"id": "orders-predisc", "title": "Order records pre-discount total",
         "old": '"total": cart["total"], "status"', "new": '"total": cart["subtotal"], "status"'},
        {"id": "orders-typo", "title": "Typo in order status",
         "old": '"status": "confirmed"}', "new": '"status": "confirmd"}'},
    ],
}
