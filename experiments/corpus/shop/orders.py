"""Working out what a customer actually pays."""

from customers import find_customer
from pricing import add_gst, apply_discount


def order_total(customer_id, base_amount):
    """Return the final amount payable, after discount and GST.

    Returns 0 if the customer does not exist.
    """
    customer = find_customer(customer_id)
    if customer is None:
        return 0

    after_discount = apply_discount(base_amount, customer["tier"])
    return round(add_gst(after_discount), 2)


def savings(customer_id, base_amount):
    """How much the customer saved against the undiscounted price."""
    customer = find_customer(customer_id)
    if customer is None:
        return 0
    after_discount = apply_discount(base_amount, customer["tier"])
    return round(base_amount - after_discount, 2)
