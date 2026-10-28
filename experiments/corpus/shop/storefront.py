"""The customer-facing checkout page.

Plain HTML on purpose: a demonstration needs something a non-programmer can
read at a glance. A JSON response does not communicate that a customer has been
overcharged; a receipt showing the wrong total does.
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ShopMind — Checkout</title>
<style>
  body{{margin:0;background:#0f1115;color:#e6e9ef;
       font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
  .wrap{{max-width:560px;margin:40px auto;padding:0 20px}}
  .card{{background:#171a21;border:1px solid #2a2f3a;border-radius:14px;
         padding:28px 32px;margin-bottom:18px}}
  h1{{margin:0 0 4px;font-size:22px;letter-spacing:-.02em}}
  .muted{{color:#9aa3b2;font-size:14px}}
  .row{{display:flex;justify-content:space-between;padding:11px 0;
        border-bottom:1px solid #2a2f3a;font-size:15px}}
  .row:last-of-type{{border-bottom:none}}
  .tier{{display:inline-block;background:#3b2f00;color:#ffc53d;font-size:12px;
         font-weight:700;padding:2px 10px;border-radius:999px;margin-left:8px;
         letter-spacing:.04em}}
  .total{{display:flex;justify-content:space-between;align-items:baseline;
          margin-top:18px;padding-top:18px;border-top:2px solid #2a2f3a}}
  .total .label{{font-size:15px;color:#9aa3b2}}
  .total .amt{{font-size:38px;font-weight:700;letter-spacing:-.03em;color:{colour}}}
  .flag{{background:#2a1416;border:1px solid #5c2328;color:#ff8f8f;
         border-radius:10px;padding:14px 18px;font-size:14px;margin-top:16px}}
  .ok{{background:#10241a;border:1px solid #1f5137;color:#6ee7a8;
       border-radius:10px;padding:14px 18px;font-size:14px;margin-top:16px}}
  a{{color:#5b9dff}}
</style></head><body><div class="wrap">

  <div class="card">
    <h1>ShopMind</h1>
    <div class="muted">Order #4821 &middot; Checkout</div>
  </div>

  <div class="card">
    <div class="row"><span>Customer</span>
      <span><b>{name}</b><span class="tier">{tier} &mdash; {discount}% off</span></span></div>
    <div class="row"><span>Wireless Headphones &times; 1</span><span>&#8377;{base:,.2f}</span></div>
    <div class="row"><span>Member discount ({discount}%)</span><span>&minus; &#8377;{saved:,.2f}</span></div>

    <div class="total">
      <span class="label">Amount to pay</span>
      <span class="amt">&#8377;{total:,.2f}</span>
    </div>

    {verdict}
  </div>

  <div class="muted" style="text-align:center">
    Try: <a href="/shop?customer=101">gold</a> &middot;
    <a href="/shop?customer=102">silver</a> &middot;
    <a href="/shop?customer=103">regular</a> &middot;
    <a href="/shop?customer=999">unknown customer</a>
  </div>

</div></body></html>"""


def render(name, tier, discount, base, saved, total, expected):
    """Render the receipt, flagging a total that cannot be right.

    The check is deliberately naive -- "you cannot be asked to pay more than the
    undiscounted price" -- because the point is that ANYONE can see it is wrong,
    not that the page is clever.
    """
    if total > base:
        verdict = (f'<div class="flag"><b>This cannot be right.</b> The order is '
                   f'&#8377;{base:,.2f} and the customer gets {discount}% off, so '
                   f'they should pay &#8377;{expected:,.2f} &mdash; not '
                   f'&#8377;{total:,.2f}.</div>')
        colour = "#ff6b6b"
    else:
        verdict = ('<div class="ok">Total looks correct: '
                   f'{discount}% off &#8377;{base:,.2f}.</div>')
        colour = "#3ddc97"

    return PAGE.format(name=name, tier=tier.title(), discount=discount,
                       base=base, saved=saved, total=total,
                       expected=expected, verdict=verdict, colour=colour)


NOT_FOUND = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>ShopMind</title><style>
body{margin:0;background:#0f1115;color:#e6e9ef;font:16px ui-sans-serif,system-ui,sans-serif}
.wrap{max-width:560px;margin:80px auto;padding:0 20px;text-align:center}
.card{background:#171a21;border:1px solid #2a2f3a;border-radius:14px;padding:40px}
a{color:#5b9dff}
</style></head><body><div class="wrap"><div class="card">
<h2>Customer not found</h2>
<p style="color:#9aa3b2">No customer with that id.</p>
<p><a href="/shop?customer=101">Back to a valid order</a></p>
</div></div></body></html>"""
