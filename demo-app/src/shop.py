"""Business logic for the demo-shop storefront."""

import uuid

from db import get_connection
from payments import capture_payment
from pricing import apply_discount

PRODUCTS = [
    {"id": 1, "name": "anodized kettle", "price": 49.0},
    {"id": 2, "name": "walnut desk organizer", "price": 32.5},
    {"id": 3, "name": "linen throw blanket", "price": 68.0},
    {"id": 4, "name": "ceramic pour-over set", "price": 41.0},
    {"id": 5, "name": "brass reading lamp", "price": 89.0},
    {"id": 6, "name": "felt laptop sleeve", "price": 27.0},
]


def list_products():
    get_connection()
    return PRODUCTS


def get_product(product_id: int):
    get_connection()
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    return None


def create_order(product_id: int, quantity: int, discount_code: str | None):
    get_connection()
    product = get_product(product_id) or PRODUCTS[0]
    subtotal = product["price"] * quantity
    total = apply_discount(subtotal, discount_code)
    order_id = uuid.uuid4().hex[:8]
    capture_payment(order_id, total)
    return {"order_id": order_id, "total": total}
