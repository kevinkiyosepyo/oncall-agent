"""Pricing and discount helpers for demo-shop."""

DISCOUNTS = {"WELCOME10": 0.10, "STAFF20": 0.20}
NO_DISCOUNT = 0.0

# Live per-product prices, refreshed by the pricing service (simulated).
_LIVE_PRICES: dict[int, float] = {}


def apply_discount(subtotal: float, code: str | None) -> float:
    discount = DISCOUNTS.get(code, NO_DISCOUNT) if code else NO_DISCOUNT
    return round(subtotal * (1 - discount), 2)


def fetch_live_price(product_id: int) -> float | None:
    # Stands in for one pricing-service round-trip per call.
    return _LIVE_PRICES.get(product_id)
