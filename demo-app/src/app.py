"""demo-shop: a deliberately breakable toy storefront API.

Business logic lives in shop.py / pricing.py / db.py — the same files that
seed the evidence repo the chaos runner commits bad changes to. Failures are
triggered at runtime via /admin/fault so the running service stays
deterministic; the evidence repo carries the corresponding code change, and
the fault's error strings mirror what that code change would produce.
"""

import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import shop

app = FastAPI(title="demo-shop")

FAULTS: dict[str, bool] = {
    "checkout_error": False,
    "latency": False,
    "db_pool": False,
    "provider_timeout": False,
}

DB_POOL_ERROR = (
    "sqlalchemy.exc.TimeoutError: QueuePool limit of size 2 overflow 0 "
    "reached, connection timed out after 30.00s (db.py, in get_connection)"
)

PROVIDER_ERROR = (
    "httpx.ReadTimeout: POST https://api.paymock.io/v2/charges timed out "
    "after 10.0s (payments.py, in capture_payment) — 3 retries exhausted"
)

LOG_FILE = os.environ.get("LOG_FILE", "/var/log/demo/access.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

_ID_SEGMENT = re.compile(r"/\d+")


def _normalize(path: str) -> str:
    return _ID_SEGMENT.sub("/{id}", path)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    path = _normalize(request.url.path)
    if not path.startswith(("/admin", "/healthz")):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "error": getattr(request.state, "error", None),
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    return response


def _pool_error(request: Request) -> JSONResponse:
    request.state.error = DB_POOL_ERROR
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/products")
async def list_products(request: Request):
    if FAULTS["db_pool"]:
        return _pool_error(request)
    if FAULTS["latency"]:
        # one simulated pricing-service round-trip per catalog item
        await asyncio.sleep(0.55 * len(shop.PRODUCTS))
    return {"products": shop.list_products()}


@app.get("/products/{product_id}")
async def get_product(product_id: int, request: Request):
    if FAULTS["db_pool"]:
        return _pool_error(request)
    product = shop.get_product(product_id)
    if product is None:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return product


class CheckoutBody(BaseModel):
    product_id: int = 1
    quantity: int = 1
    discount_code: str | None = None


@app.post("/checkout")
async def checkout(body: CheckoutBody, request: Request):
    if FAULTS["db_pool"]:
        return _pool_error(request)
    if FAULTS["provider_timeout"]:
        request.state.error = PROVIDER_ERROR
        return JSONResponse(
            status_code=502, content={"detail": "payment provider unavailable"}
        )
    if FAULTS["checkout_error"] and body.discount_code not in ("WELCOME10", "STAFF20"):
        # mirrors DISCOUNTS[code] raising for absent/None codes
        request.state.error = (
            f"KeyError: {body.discount_code!r} in apply_discount "
            "(pricing.py, in apply_discount) — discount code missing from "
            "DISCOUNTS table"
        )
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
    return shop.create_order(body.product_id, body.quantity, body.discount_code)


class FaultBody(BaseModel):
    name: str
    enabled: bool


@app.post("/admin/fault")
async def set_fault(body: FaultBody):
    if body.name not in FAULTS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"unknown fault {body.name!r}; known: {sorted(FAULTS)}"},
        )
    FAULTS[body.name] = body.enabled
    return {"faults": FAULTS}


@app.get("/admin/faults")
async def get_faults():
    return {"faults": FAULTS}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
