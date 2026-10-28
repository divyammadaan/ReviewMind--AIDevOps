"""Discounts and tax."""

# Discount for each tier, as a PERCENTAGE out of 100.
# "gold": 20 means gold customers get 20% off.
TIER_DISCOUNTS = {"gold": 20, "silver": 10, "regular": 0}

GST_PERCENT = 18


def discount_percent(tier):
    """Return the discount for a tier as a PERCENTAGE from 0 to 100.

    Gold returns 20, meaning twenty percent off -- NOT 0.20.
    Unknown tiers get no discount.
    """
    return TIER_DISCOUNTS.get(tier, 0)


def apply_discount(amount, tier):
    """Return the amount after the tier's discount has been taken off."""
    percent = discount_percent(tier)
    return amount - (amount * percent / 100)


def add_gst(amount):
    """Add GST to an amount."""
    return amount + (amount * GST_PERCENT / 100)
