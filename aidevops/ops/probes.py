"""Black-box health probes. Each returns None when healthy, or raises Fail(detail).

The detail string is what the fixer is told about the symptom, so it states what
was expected and what came back.
"""
import httpx

URL = {"catalog": "http://catalog:9101", "inventory": "http://inventory:9102",
       "cart": "http://cart:9103", "payments": "http://payments:9104",
       "orders": "http://orders:9105", "gateway": "http://gateway:8080"}
DEPS = {"catalog": [], "inventory": [], "payments": [], "cart": ["catalog"],
        "orders": ["cart", "inventory", "payments"], "gateway": []}
T = 2.5


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def _ok(r, what):
    check(r.status_code == 200, f"{what} -> HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


def _restore_stock(before):
    """Put back whatever a probe reserved, so probing never drains the shop."""
    u = URL["inventory"]
    now = httpx.get(f"{u}/stock/1", timeout=T)
    if now.status_code == 200 and now.json()["in_stock"] < before:
        httpx.post(f"{u}/restock", json={"product_id": 1, "qty": before - now.json()["in_stock"]}, timeout=T)


def catalog():
    u = URL["catalog"]
    items = _ok(httpx.get(f"{u}/products", timeout=T), "GET /products")
    check(len(items) == 6, f"GET /products expected 6 products, got {len(items)}")
    one = _ok(httpx.get(f"{u}/products/1", timeout=T), "GET /products/1")
    check(one["price"] == 2999, f"GET /products/1 price expected 2999, got {one['price']}")
    check(httpx.get(f"{u}/products/999", timeout=T).status_code == 404,
          "GET /products/999 expected HTTP 404")


def inventory():
    u = URL["inventory"]
    before = _ok(httpx.get(f"{u}/stock/1", timeout=T), "GET /stock/1")["in_stock"]
    over = httpx.post(f"{u}/reserve", json={"product_id": 1, "qty": 10**6}, timeout=T)
    check(over.status_code == 409,
          f"POST /reserve of 1,000,000 units expected HTTP 409, got {over.status_code}")
    try:
        res = _ok(httpx.post(f"{u}/reserve", json={"product_id": 1, "qty": 2}, timeout=T),
                  "POST /reserve qty=2")
        check(res["remaining"] == before - 2,
              f"after reserving 2 of {before}, remaining expected {before - 2}, got {res['remaining']}")
    finally:
        _restore_stock(before)


def cart():
    u = URL["cart"]
    httpx.delete(f"{u}/cart/probe-cart", timeout=T)
    try:
        for _ in range(2):
            _ok(httpx.post(f"{u}/cart/probe-cart/add", json={"product_id": 1, "qty": 1}, timeout=T),
                "POST /cart/probe-cart/add")
        c = _ok(httpx.get(f"{u}/cart/probe-cart", timeout=T), "GET /cart/probe-cart")
        check(c["items"] and c["items"][0]["qty"] == 2,
              f"adding product 1 twice (qty 1 each) expected qty 2, got {c['items']}")
        check(c["subtotal"] == 5998, f"subtotal expected 5998, got {c['subtotal']}")
        check(abs(c["discount"] - 599.8) < 0.01 and abs(c["total"] - 5398.2) < 0.01,
              f"subtotal 5998 (>=5000, 10% off) expected discount 599.8 and total 5398.2, "
              f"got discount {c['discount']} and total {c['total']}")
    finally:
        httpx.delete(f"{u}/cart/probe-cart", timeout=T)


def payments():
    u = URL["payments"]
    ok = _ok(httpx.post(f"{u}/charge", json={"user": "probe", "amount": 100}, timeout=T), "POST /charge 100")
    check(ok["status"] == "approved" and ok["txn_id"],
          f"charge of 100 expected status 'approved' with a txn_id, got {ok}")
    for amount in (5398.2, 99999):
        big = _ok(httpx.post(f"{u}/charge", json={"user": "probe", "amount": amount}, timeout=T),
                  f"POST /charge {amount}")
        check(big["status"] == "approved", f"charge of {amount} expected status 'approved', got '{big['status']}'")
    over = httpx.post(f"{u}/charge", json={"user": "probe", "amount": 100001}, timeout=T)
    check(over.status_code == 402, f"charge of 100001 expected HTTP 402 (over limit), got {over.status_code}")
    zero = httpx.post(f"{u}/charge", json={"user": "probe", "amount": 0}, timeout=T)
    check(zero.status_code == 400, f"charge of 0 expected HTTP 400, got {zero.status_code}")


def orders():
    u = URL["orders"]
    before = httpx.get(f"{URL['inventory']}/stock/1", timeout=T).json()["in_stock"]
    httpx.delete(f"{URL['cart']}/cart/probe-order", timeout=T)
    try:
        _ok(httpx.post(f"{URL['cart']}/cart/probe-order/add", json={"product_id": 1, "qty": 2}, timeout=T),
            "setup: add to cart")
        o = _ok(httpx.post(f"{u}/checkout/probe-order", timeout=T), "POST /checkout/probe-order")
        check(o["status"] == "confirmed", f"order status expected 'confirmed', got '{o['status']}'")
        check(abs(o["total"] - 5398.2) < 0.01,
              f"order total expected 5398.2 (discounted), got {o['total']}")
    finally:
        _restore_stock(before)


def gateway():
    check(httpx.get(f"{URL['gateway']}/health", timeout=T).status_code == 200, "gateway /health not 200")


PROBES = {"catalog": catalog, "inventory": inventory, "cart": cart,
          "payments": payments, "orders": orders, "gateway": gateway}
