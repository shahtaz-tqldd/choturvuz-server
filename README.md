# Choturvuz — Fashion E-commerce API

A Django REST API for a clothing/fashion brand storefront with guest checkout,
product variants, collections, order tracking, and bKash payments (full or half).

Built on a cleaned-up Django + DRF + Celery + PostgreSQL boilerplate.

## Features

- **Catalog** — products with variants (size × color, SKU, stock, optional per-variant price),
  multiple images, fabric/care info, sale prices, and many-to-many collections.
  Deleting a collection never deletes its products.
- **Custom orders** — staff publish design templates (products with `kind=CUSTOM`: jerseys,
  tees, hoodies) with a design code, min quantity, sizes and colours. Customers upload their
  designs/logos, add instructions and size guidelines, and order quantities per size × colour.
  Staff review and send a quote; the order then follows the normal payment/fulfilment flow.
  See [`docs/FRONTEND_CUSTOM_ORDERS.md`](docs/FRONTEND_CUSTOM_ORDERS.md).
- **Media** — images and files are stored on Cloudinary.
- **Reviews** — store-wide customer reviews. Customers submit them (optionally with an order
  number for a "verified purchase" badge); staff approve, hide, feature or add their own.
  See [`docs/FRONTEND_REVIEWS.md`](docs/FRONTEND_REVIEWS.md).
- **Guest orders** — no account needed; email or phone identifies the customer.
  Prices are always computed server-side; stock is validated at checkout.
- **Order tracking** — customers track with order number + email/phone and see a full
  status timeline (pending payment → confirmed → processing → shipped → delivered / cancelled).
- **Payments** — bKash tokenized checkout. Customers pay in FULL, or HALF advance with the
  rest due on delivery (recorded by an admin). Stock is reserved when payment settles.
- **Admin APIs** — manage products/variants/images, collections (create, assign products),
  orders (view, status transitions, record offline payments), and payments.
- **Django admin** is also registered for every model as a fallback panel.
- **Maintenance** — a daily Celery task cancels stale unpaid orders (>24h) and cleans
  deactivated accounts.

## Running locally

```bash
python -m venv env && source env/bin/activate
pip install -r requirements.txt
cp .env.example .env          # adjust DB + bKash credentials
./env/bin/python manage.py migrate
./env/bin/python manage.py create_superuser
./env/bin/python manage.py seed_demo          # optional demo catalog
./env/bin/python manage.py runserver
```

### Demo data

`seed_demo` loads 5 collections, 8 products, 6 custom order templates and 4 reviews from
[`base/demo/demo_products.json`](base/demo/demo_products.json). Edit that file (or pass
`--file other.json`) to seed a different catalog.

- `seed_demo`: creates whatever is missing. Re-running it never duplicates anything.
- `seed_demo --reset`: deletes the file's items and recreates them. Products that already have orders are kept.
- `seed_demo --dry-run`: validates the file without saving.
- `seed_demo --once`: what app startup runs. It seeds only on a fresh database (no products or
  collections), at most once ever, so demo items you delete never come back.
- In Docker (`./startapp.sh`) the API container runs `seed_demo --once` on every start, right after
  `create_superuser`. Set `DJANGO_SEED_DEMO=0` in `.env` to turn it off.

Everything it creates is ordinary data, so you can edit or delete it from the admin.

Or with Docker: `./startapp.sh` (dev compose: API, worker, beat, Postgres, Redis).

Postgres/Redis must be reachable (`DB_*`, `CELERY_BROKER_URL` in `.env`).

## Cloudinary setup

```
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
# or: CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>
CLOUDINARY_ROOT_FOLDER=choturvuz
```

All assets go under `CLOUDINARY_ROOT_FOLDER` (`images/`, `files/`, `custom-orders/`). To serve
customer PDFs/ZIPs, enable "Allow delivery of PDF and ZIP files" in Cloudinary's security settings.

## bKash setup (tokenized checkout)

Fill these in `.env`:

```
BKASH_BASE_URL=https://tokenized.sandbox.bka.sh/v1.2.0-beta
BKASH_APP_KEY=...
BKASH_APP_SECRET=...
BKASH_USERNAME=...
BKASH_PASSWORD=...
BKASH_CALLBACK_URL=http://localhost:8000/api/v1/payments/bkash/callback/
```

Until configured, order creation/tracking still work; initiating payment returns a clear error.

## API overview

A ready-to-import Postman collection lives at [`docs/choturvuz_postman.json`](docs/choturvuz_postman.json)
(45 requests across client and admin folders, with sample bodies and captured-token scripts).

All responses are wrapped as `{status, success, message, data, meta?}`.
Admin endpoints require a staff JWT (`Authorization: Bearer <token>` from `/api/v1/admin/accounts/login/`).

### Client (no auth)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/catalog/products/` | List products (`search`, `collection`, `gender`, `featured`, `on_sale`, `ordering`) |
| GET | `/api/v1/catalog/products/<slug|id>/` | Product detail with variants |
| GET | `/api/v1/catalog/collections/` | List collections |
| GET | `/api/v1/catalog/collections/<slug>/` | Collection with its products |
| POST | `/api/v1/orders/` | Place an order (guest; email or phone required) |
| GET | `/api/v1/orders/track/?order_number=&email=|phone=` | Track an order + timeline |
| POST | `/api/v1/orders/<order_number>/pay/` | Start bKash payment → `payment_url` |
| GET | `/api/v1/payments/bkash/callback/` | bKash redirect target (executes & redirects to storefront) |
| GET | `/api/v1/catalog/custom-templates/` | List custom order templates |
| GET | `/api/v1/catalog/custom-templates/<slug|id|design_code>/` | Template detail |
| POST | `/api/v1/orders/custom/uploads/` | Upload a design/logo file (multipart `file`) |
| POST | `/api/v1/orders/custom/estimate/` | Indicative price for a custom order |
| POST | `/api/v1/orders/custom/` | Place a custom order (goes to review) |
| POST | `/api/v1/orders/custom/<order_number>/attachments/` | Customer adds files later |
| GET/POST | `/api/v1/reviews/` | Approved reviews / submit a review (goes to moderation) |
| GET | `/api/v1/reviews/summary/` | Average rating and star distribution |

Order body:

```json
{
  "customer_name": "Nusrat Jahan",
  "customer_email": "nusrat@example.com",
  "customer_phone": "+8801711223344",
  "address_line": "House 12, Road 5, Dhanmondi",
  "city": "Dhaka",
  "payment_plan": "HALF",
  "items": [{"variant_id": "<uuid>", "quantity": 2}]
}
```

### Admin

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET/POST | `/api/v1/admin/catalog/products/` | List/create products (nested variants, images, `collection_ids`) |
| GET/PATCH/DELETE | `/api/v1/admin/catalog/products/<id>/` | Product detail/update/delete |
| GET/POST | `/api/v1/admin/catalog/collections/` | List/create collections |
| GET/PATCH/DELETE | `/api/v1/admin/catalog/collections/<id>/` | Collection detail/update/delete |
| POST | `/api/v1/admin/catalog/collections/<id>/products/` | `{action: add|remove|set, product_ids: []}` |
| GET | `/api/v1/admin/orders/` | Orders list (`search`, `status`, `payment_status`, `date_from/to`) |
| GET/PATCH | `/api/v1/admin/orders/<id>/` | Order detail / status transition `{status, note}` |
| POST | `/api/v1/admin/orders/<id>/record-payment/` | Record offline payment `{amount, note}` |
| POST | `/api/v1/admin/orders/<id>/quote/` | Quote a custom order (final prices, fees) |
| GET/POST | `/api/v1/admin/orders/<id>/attachments/` | List / add order files (e.g. mockups) |
| DELETE | `/api/v1/admin/orders/<id>/attachments/<attachment_id>/` | Remove an order file |
| GET/POST | `/api/v1/admin/reviews/` | All reviews (`status`, `rating`, `search`…) / add a review |
| GET/PATCH/DELETE | `/api/v1/admin/reviews/<id>/` | Review detail / edit, approve, hide, feature / delete |
| POST | `/api/v1/admin/reviews/bulk/` | `{ids, action: approve|hide|pending|feature|unfeature|delete}` |
| GET | `/api/v1/admin/payments/` | Payment attempts (`status`, `method`, `search`) |
| PATCH | `/api/v1/admin/base/update/` | Store config (branding, delivery fees, half-payment toggle, policies) |

### Order lifecycle

`PENDING_PAYMENT → CONFIRMED → PROCESSING → SHIPPED → DELIVERED`, with CANCELLED reachable
from any active state (restores reserved stock, flags refunds). Illegal jumps are rejected.

Custom orders start at `PENDING_REVIEW` (cancel only) and enter the flow above when staff send a
quote (`PENDING_PAYMENT`, or `PENDING_CONFIRMATION` for COD). They never touch stock. Unpaid
quoted custom orders expire `CUSTOM_ORDER_QUOTE_EXPIRY_HOURS` (72) after the quote.

### Payment plans

- `FULL` — the entire total is charged to bKash.
- `HALF` — 50% advance via bKash; the remainder is collected on delivery and recorded
  by an admin (`record-payment`). Toggle half payments from the store config.

## Tests

```bash
./env/bin/python manage.py test app accounts base catalog orders reviews
```

Tests cover the catalog APIs, guest ordering, tracking, lifecycle transitions,
stock handling, and the mocked bKash flow.

## Notes

- Money is stored as `Decimal(12,2)` in BDT; delivery fee differs inside/outside Dhaka
  (store config, defaults 70/130).
- Order numbers look like `ARS-261103-8FJ2QA`.
- Product images are uploaded through the staff file API (`/api/v1/base/file/upload/`, Cloudinary)
  and stored as URLs, so the catalog stays storage-agnostic.
