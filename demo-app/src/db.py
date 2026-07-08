"""Simulated database access layer for demo-shop."""

# Connection pool sizing. Checkout bursts hold a dozen connections during
# flash sales, so keep this generous.
POOL_SIZE = 20
POOL_OVERFLOW = 10


class _Connection:
    def execute(self, query: str):
        return []


def get_connection() -> _Connection:
    """Hand out a pooled connection (simulated)."""
    return _Connection()
