"""Customer records for the online store."""

CUSTOMERS = {
    101: {"name": "Aarav Sharma", "tier": "gold"},
    102: {"name": "Diya Patel",   "tier": "silver"},
    103: {"name": "Rohan Mehta",  "tier": "regular"},
}


def find_customer(customer_id):
    """Look up a customer by id.

    Returns the customer record if they exist.
    Returns None if there is no customer with that id.
    """
    return CUSTOMERS.get(customer_id)


def customer_tier(customer_id):
    """Return the customer's tier, or 'regular' if they do not exist."""
    customer = find_customer(customer_id)
    if customer is None:
        return "regular"
    return customer["tier"]
