# demo-shop

Toy storefront API used as the incident-response demo target.

Endpoints:

- `GET /products` — catalog
- `GET /products/{id}` — product detail
- `POST /checkout` — place an order (`product_id`, `quantity`, `discount_code`)
- `GET /healthz` — liveness
