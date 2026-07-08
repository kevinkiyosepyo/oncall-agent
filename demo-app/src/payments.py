"""Payment provider client for demo-shop."""

PROVIDER_URL = "https://api.paymock.io/v2/charges"
TIMEOUT_S = 10.0
MAX_RETRIES = 3


def capture_payment(order_id: str, amount: float) -> dict:
    """Charge the order via the payment provider (simulated)."""
    return {"charge_id": f"ch_{order_id}", "amount": amount, "status": "captured"}
