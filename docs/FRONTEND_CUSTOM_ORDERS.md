# Choturvuz API changes: frontend integration guide

This guide covers the backend changes made when Hotasha became Choturvuz, and what the
**storefront** and **admin panel** need to change. It covers:

1. **Cloudinary** replaces Cloudflare R2 for all images and files.
2. **Custom order templates**: products marked `kind: "CUSTOM"` (jerseys, tees and hoodies for
   campus groups and tours). Customers order them with their own artwork, sizes and quantities.
3. **Custom orders**: a new order type with a review-and-quote step. After the quote it uses the
   existing payment, fulfilment and tracking flow.

All responses still use the envelope `{status, success, message, data, meta?, errors?}`.
Validation errors return `400` with `errors: {field: [messages]}`, and `message` holds the first
error.

---

## 0. Changes that affect existing screens

| Area | Change | Action |
| --- | --- | --- |
| Image URLs | All new uploads are served from `https://res.cloudinary.com/<cloud>/...` | Add `res.cloudinary.com` to the Next.js `images.remotePatterns` setting. |
| `GET /catalog/products/` | Returns **regular products only** by default. | No change needed for the shop grid. Pass `kind=ALL` to include templates. |
| Collections | `product_count` now counts **regular** products only. A new `custom_template_count` field was added. | Show a "Custom" badge or tab when `custom_template_count > 0`. |
| `GET /catalog/collections/<slug>/` | A new `custom_templates` array sits next to `products`. | Render it as its own section. |
| Product payloads | New fields: `kind`, `design_code`, `min_order_quantity`. The detail payload also has `custom_sizes`, `custom_colors`, `size_chart_url`, `production_days`. | Send `kind === "CUSTOM"` to the custom order page, not to add-to-cart. |
| Sizes | New size `XXXL`, labelled **3XL**. | Add it to the size label maps and size filters. |
| Orders | New status `PENDING_REVIEW` and new fields (see §3.6). | Update status badges and timeline labels. |
| Order items | New fields: `size`, `color_name`, `color_hex`, `note`. | Show `note` for custom lines (names and numbers). |
| `POST /base/file/upload/` (admin) | The response adds `resource_type`. `key` is now the Cloudinary public id. | Keep using `file_url` as before. |

### Cloudinary delivery tips

Stored URLs look like this:

```
https://res.cloudinary.com/<cloud>/image/upload/v1712345678/choturvuz/images/products/abc123.jpg
```

To get a resized, auto-format image, put transformations right after `/upload/`. For example:

```ts
const cld = (url: string, t = "f_auto,q_auto,w_800") =>
  url.includes("/upload/") ? url.replace("/upload/", `/upload/${t}/`) : url;
```

Keep the original URL whenever you send it back to the API. Never send a transformed one.

> PDF/AI/PSD previews: Cloudinary treats PDFs, AI, EPS and PSD files as images. You can preview
> page 1 as a JPG by changing the extension to `.jpg` (or with `pg_1,f_jpg`). ZIP files are
> "raw": offer them as a download only. PDFs and ZIPs are only delivered when **"Allow delivery of
> PDF and ZIP files"** is enabled under Cloudinary → Settings → Security.

---

## 1. Storefront: browsing custom templates

### 1.1 List templates

`GET /api/v1/catalog/custom-templates/`

It takes the same query params as the product list: `search` (also matches an exact
`design_code`), `collection`, `gender`, `featured`, `min_price`, `max_price`, `ordering`, `page`,
`page_size`.

```json
{
  "data": [
    {
      "id": "5d1c…",
      "name": "Department Jersey",
      "slug": "department-jersey",
      "kind": "CUSTOM",
      "design_code": "JR-024",
      "min_order_quantity": 10,
      "base_price": "450.00",
      "sale_price": null,
      "effective_price": "450.00",
      "discount_percent": 0,
      "in_stock": true,
      "primary_image": "https://res.cloudinary.com/…",
      "secondary_image": "",
      "collections": ["jerseys"],
      "…": "same card fields as products"
    }
  ],
  "meta": { "count": 1, "page": 1, "page_size": 20, "num_pages": 1, "next": null, "previous": null }
}
```

`in_stock` is always `true` for templates, because they are made to order. Show "From ৳450 / piece
· Min 10 pcs" rather than stock information.

You can also use `GET /api/v1/catalog/products/?kind=CUSTOM`, or `?kind=ALL` for a mixed grid.

### 1.2 Template detail

`GET /api/v1/catalog/custom-templates/<slug | id | design_code>/`

A customer can type a design code (for example `JR-024`) into a search box. Send it to this
endpoint directly.

Extra fields compared with a product card:

```json
{
  "description": "…",
  "highlights": ["Sublimation print", "Breathable mesh"],
  "care_instructions": "…",
  "custom_sizes": ["M", "L", "XL", "XXL", "XXXL"],
  "custom_colors": [
    { "name": "Navy", "hex": "#1f2a44" },
    { "name": "White", "hex": "#ffffff" }
  ],
  "size_chart_url": "https://res.cloudinary.com/…/size-chart.png",
  "production_days": 7,
  "images": [{ "id": "…", "image_url": "…", "alt_text": "", "position": 0 }],
  "variants": []
}
```

How to read these fields:
- An empty `custom_sizes` list means the customer may leave size blank or pick any size (`XS`, `S`,
  `M`, `L`, `XL`, `XXL`, `XXXL`, `ONE_SIZE`).
- An empty `custom_colors` list means the customer can type any colour name, and optionally a hex
  value.
- `variants` is always empty for templates.

`GET /api/v1/catalog/products/<slug>/` also returns templates, with `kind: "CUSTOM"`. Use `kind` to
decide which page to render.

---

## 2. Storefront: the custom order builder

The flow on the page:

```
Template page ──► Builder form ──► (1) upload files ──► (2) live estimate ──► (3) submit
                                                                                   │
              Track page ◄── (5) pay after the quote arrives ◄── (4) staff review & quote
```

### 2.1 Upload design files (one request per file)

`POST /api/v1/orders/custom/uploads/` sends `multipart/form-data` with the field `file`. No auth is
needed.

- Allowed types: `jpg jpeg png webp gif svg heic tif tiff pdf ai eps psd zip`
- Max size: 10 MB by default (`CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB`)
- Rate limit: 40 uploads per hour per IP by default. Exceeding it returns `429`.
- Call it **from the browser**, not through the Next.js server. Otherwise every customer shares the
  server's IP for the rate limit, and the file travels through the server twice.

```json
// 201
{
  "data": {
    "url": "https://res.cloudinary.com/<cloud>/image/upload/v1712/choturvuz/custom-orders/uploads/2026-11/9f3c….png",
    "public_id": "choturvuz/custom-orders/uploads/2026-11/9f3c…",
    "resource_type": "image",
    "format": "png",
    "file_name": "cse-logo.png",
    "content_type": "image/png",
    "file_size": 183442
  }
}
```

Keep `url` and `file_name` in the form state. Show a thumbnail for `resource_type: "image"`, and a
file chip for `raw`.

### 2.2 Live estimate (optional, recommended)

`POST /api/v1/orders/custom/estimate/`

```json
{
  "template_id": "5d1c…",
  "city": "Dhaka",
  "payment_plan": "HALF",
  "items": [
    { "size": "M", "color_name": "Navy", "quantity": 6, "note": "Rafi #7, Mitu #10, …" },
    { "size": "L", "color_name": "Navy", "quantity": 4 }
  ]
}
```

```json
// 200
{
  "data": {
    "template_id": "5d1c…",
    "design_code": "JR-024",
    "unit_price": "450.00",
    "total_quantity": 10,
    "min_order_quantity": 10,
    "subtotal": "4500.00",
    "delivery_fee": "70.00",
    "total": "4570.00",
    "advance_amount": "2285.00",
    "payment_plan": "HALF",
    "is_estimate": true,
    "items": [
      { "size": "M", "color_name": "Navy", "color_hex": "#1f2a44", "variant_name": "Navy / M",
        "quantity": 6, "note": "Rafi #7, …", "unit_price": "450.00", "line_total": "2700.00" }
    ]
  }
}
```

Debounce this call (about 400 ms) while the customer edits lines. The same rules apply as on
submit, so you can show errors inline (see §2.4).

Label the total as an **estimate**: "Final price confirmed after design review." Staff may change
it, for example by adding printing costs.

### 2.3 Submit the custom order

`POST /api/v1/orders/custom/`

```json
{
  "customer_name": "Rafi Ahmed",
  "customer_email": "rafi@example.com",
  "customer_phone": "+8801711000000",
  "address_line": "CSE Building, University of Dhaka",
  "city": "Dhaka",
  "postal_code": "",
  "notes": "",
  "payment_plan": "HALF",

  "template_id": "5d1c…",
  "items": [
    { "size": "M", "color_name": "Navy", "quantity": 6, "note": "Names/numbers: Rafi 7, Mitu 10, …" },
    { "size": "L", "color_name": "Navy", "quantity": 4, "note": "" }
  ],
  "attachments": [
    { "url": "<url from 2.1>", "kind": "LOGO", "file_name": "cse-logo.png", "note": "Chest, 3 inch" },
    { "url": "<url from 2.1>", "kind": "DESIGN", "file_name": "back.pdf" }
  ],
  "custom_instructions": "Logo on left chest, batch name 'CSE-27' on the back in white.",
  "size_guidelines": "Slim fit. M = 38in chest, L = 40in.",
  "needed_by": "2026-12-10"
}
```

| Field | Rules |
| --- | --- |
| `customer_email` / `customer_phone` | At least one is required, the same as regular checkout. |
| `payment_plan` | `FULL`, `HALF` or `COD`, subject to the store config toggles. It can be changed later by staff in the quote. |
| `template_id` | Must be an ACTIVE template with `kind=CUSTOM`. |
| `items[]` | 1 to 100 lines. Each line has `size` (required when the template lists `custom_sizes`), `color_name` (must match one of `custom_colors` when the template lists any; matching ignores case), `color_hex` (optional, only used when the template has no colour list), `quantity` (1 to 1000) and `note` (up to 500 characters, for names or jersey numbers). The same size and colour may repeat with different notes. |
| total quantity | Must be at least the template's `min_order_quantity`. |
| `attachments[]` | Optional, up to 10 files. Each `url` **must** come from `/orders/custom/uploads/`; any other URL is rejected. `kind` is one of `DESIGN`, `LOGO`, `REFERENCE`, `SIZE_GUIDE`, `OTHER`. |
| `custom_instructions`, `size_guidelines` | Optional free text, up to 5000 characters each. |
| `needed_by` | Optional `YYYY-MM-DD` date. It cannot be in the past. |

The response is `201` with the full order (the same shape as `/orders/track/`, see §3.6):
`status: "PENDING_REVIEW"`, `is_awaiting_quote: true`, `requires_online_payment: false`.
Redirect to `/orders/track?order_number=…`. There is **no payment step yet**.

Rate limit: this shares the per-phone limit with regular checkout (5 orders per hour per phone by
default).

### 2.4 Errors you should show inline

All of these return `400`. For template-rule errors, `errors` names the field (`items`,
`attachments`, `template_id` or `payment_plan`), and `message` is ready to display.

| Situation | Example `message` |
| --- | --- |
| Too few pieces | `This design needs at least 10 pieces (you asked for 6).` |
| Size not offered | `Line 2: size XS is not offered for this design.` |
| Missing size | `Line 1: choose a size.` |
| Colour not offered | `Line 3: choose one of the offered colours.` |
| Foreign attachment URL | `Upload files through the custom order upload endpoint first.` |
| Template gone or draft | `This custom template is not available.` |
| Plan disabled | `Half payment is currently disabled.` |

---

## 3. Storefront: tracking a custom order

`GET /api/v1/orders/track/?order_number=…&email=…|phone=…` works the same way as before. It now
returns the new fields too.

### 3.1 Status mapping for custom orders

| `status` | Show to customer | UI |
| --- | --- | --- |
| `PENDING_REVIEW` | "Design under review" | Show the estimate, a note that staff will send the final price, and an "Add files" button. |
| `PENDING_PAYMENT` | "Quote ready, payment needed" | Show `total`, `advance_amount`, `quote_note`, and a **Pay with bKash** button (when `requires_online_payment`). |
| `PENDING_CONFIRMATION` | "Quote ready, we'll call to confirm" (COD) | Show the quote. |
| `CONFIRMED` | "Confirmed" | "Add files" is still allowed. |
| `PROCESSING` | "In production" | Show `production_days` from the template if you have it. |
| `SHIPPED` / `DELIVERED` / `CANCELLED` | Same as regular orders. | Same as regular orders. |

Use `is_custom` or `order_type === "CUSTOM"` to switch the labels (for example "Processing" becomes
"In production").

### 3.2 Paying after the quote

The existing `POST /api/v1/orders/<order_number>/pay/` works unchanged once the order is in
`PENDING_PAYMENT`. Before the quote it returns `400 This order has already been processed.`, so
show the Pay button only when `requires_online_payment` is `true`.

The bKash callback still redirects to `{USER_FRONTEND_URL}/orders/track?order_number=…&payment=…`.

> If the customer doesn't pay within **72 hours of the quote** (`CUSTOM_ORDER_QUOTE_EXPIRY_HOURS`),
> bKash-plan custom orders are cancelled automatically. Orders in review never expire. Say so next
> to the Pay button.

### 3.3 Quote information

The quote fields to show are `subtotal`, `customization_fee` (printing or setup), `discount`,
`delivery_fee`, `total`, `advance_amount` and `due_amount`, plus `quote_note` (a message from staff)
and `quoted_at`. The quote also appears as a public event in `events`, for example: `"Quote: 5070.00
BDT (Half Advance). Pay 2535.00 BDT to confirm. Includes chest embroidery."`.

Price breakdown:

```
subtotal (Σ line_total) + customization_fee − discount + delivery_fee = total
```

### 3.4 Files (customer uploads and staff mockups)

`attachments[]` on the order:

```json
{
  "id": "…", "kind": "MOCKUP", "source": "STAFF",
  "url": "https://res.cloudinary.com/…", "resource_type": "image",
  "file_name": "front-mockup.png", "note": "Front view", "created_at": "…"
}
```

Show `source: "STAFF"` + `kind: "MOCKUP"` prominently ("Preview of your print"), and the
customer's own files in a separate list.

### 3.5 Customer adds more files later

`POST /api/v1/orders/custom/<order_number>/attachments/`

This is allowed while the order is `PENDING_REVIEW`, `PENDING_PAYMENT`, `PENDING_CONFIRMATION` or
`CONFIRMED`. Upload each file through §2.1 first.

```json
{
  "phone": "01711000000",
  "attachments": [{ "url": "<from uploads>", "kind": "DESIGN", "file_name": "logo-hires.png" }]
}
```

Send `email` or `phone`, matching what the customer used on the order. The response is `201`
`{ "added": [attachments], "order": {…full order…} }`. A wrong contact returns `404`. Re-sending a
URL that is already attached is ignored, so `added` will be empty.

### 3.6 Order payload: new fields

These are added to every order response (`/orders/`, `/orders/custom/`, `/orders/track/` and the
admin detail):

```json
{
  "order_type": "CUSTOM",              // or "REGULAR"
  "is_custom": true,
  "is_awaiting_quote": true,           // status === PENDING_REVIEW
  "custom_instructions": "…",
  "size_guidelines": "…",
  "needed_by": "2026-12-10",           // or null
  "quoted_at": null,                   // ISO datetime once quoted
  "quote_note": "",
  "customization_fee": "0.00",
  "discount": "0.00",
  "attachments": [ … ],
  "items": [
    {
      "id": "…", "product_id": "…", "variant_id": null,
      "product_name": "Department Jersey", "variant_name": "Navy / M", "sku": "JR-024",
      "unit_price": "450.00", "quantity": 6, "line_total": "2700.00",
      "size": "M", "color_name": "Navy", "color_hex": "#1f2a44", "note": "Rafi 7, …"
    }
  ]
}
```

For regular orders, `customization_fee` and `discount` are `"0.00"`, `attachments` is `[]`, and the
custom fields are empty.

---

## 4. Admin panel: custom templates

Templates are ordinary products, so they use the same endpoints:
`/api/v1/admin/catalog/products/` and `/products/<id>/`.

### 4.1 Product form

Add a **"Product type"** toggle that sets `kind` to `REGULAR` or `CUSTOM`. When `kind === "CUSTOM"`:
- **Hide the variants and stock editor.** Sending variants for a template returns `400`.
- Show these fields:

| Field | Type | Notes |
| --- | --- | --- |
| `design_code` | string (≤ 40) | Unique. Saved in upper case (`jr-024` becomes `JR-024`). Use a blank value for none. |
| `min_order_quantity` | int ≥ 1 | |
| `custom_sizes` | `string[]` of size values | The server removes duplicates and sorts them (XS → XXXL). |
| `custom_colors` | `{name, hex?}[]` | Names must be unique. `hex` is `#RRGGBB`. |
| `size_chart_url` | URL | Upload with `POST /base/file/upload/` and use `file_url`. |
| `production_days` | int or null | |
| `base_price` / `sale_price` | | The starting price **per piece**. |

Images, collections, `status`, `is_featured` and the description work as they do for regular
products.

To turn an existing regular product into a template, send `{"kind": "CUSTOM", "variants": []}` in
one PATCH. Variants that already have orders can't be removed, so archive that product and create a
new template instead.

### 4.2 Lists

- `GET /admin/catalog/products/?kind=CUSTOM` lists templates. `kind=REGULAR` lists regular
  products, and `ALL` or no value lists everything. `search` also matches `design_code`.
- Admin collections now include `custom_template_count`.

---

## 5. Admin panel: custom orders

### 5.1 Orders list

`GET /api/v1/admin/orders/?order_type=CUSTOM` filters the list. It combines with the existing
`status`, `payment_status` and other filters. A **"Custom orders" tab** using
`order_type=CUSTOM&status=PENDING_REVIEW` gives staff an inbox of orders to quote.

New columns on each row: `order_type`, `total_quantity` (pieces), `needed_by` (highlight it when
the date is close) and `quoted_at`. `search` also matches a design code exactly (for example
`JR-024`).

### 5.2 Order detail

`GET /api/v1/admin/orders/<id>/` returns everything in §3.6, plus `attachments[].public_id` and
`attachments[].created_by`. For custom orders, show:

- **Brief**: the template (`items[0].product_name` and `sku`, which holds the design code),
  `custom_instructions`, `size_guidelines`, `needed_by`
- **Lines table**: colour swatch (`color_hex`), `size`, `quantity`, `note`, `unit_price`,
  `line_total`, and the total piece count
- **Files**: grouped by `source`, with download links and a delete button
- **Quote panel**: shown when the order can be quoted (see below)

### 5.3 Send or update the quote

`POST /api/v1/admin/orders/<id>/quote/`

```json
{
  "items": [
    { "id": "<order item id>", "unit_price": "500.00" },
    { "id": "<order item id>", "quantity": 5 }
  ],
  "customization_fee": "300.00",
  "discount": "100.00",
  "delivery_fee": null,
  "payment_plan": "HALF",
  "note": "Includes chest embroidery. Production 7 days after payment."
}
```

- Every field is optional. A field you leave out keeps its current value.
- `delivery_fee` left out or `null` recalculates the fee from the city.
- After the quote, the order moves to `PENDING_PAYMENT` (FULL or HALF), or to
  `PENDING_CONFIRMATION` (COD). `note` is shown to the customer.
- You can re-quote while the order is `PENDING_REVIEW`, `PENDING_PAYMENT` or `PENDING_CONFIRMATION`
  **and nothing has been paid**. Otherwise you get a `400` with a message.
- The response is the full admin order.

Show the quote panel when `is_custom && ["PENDING_REVIEW","PENDING_PAYMENT","PENDING_CONFIRMATION"].includes(status) && paid_amount === "0.00"`.
Show a live preview total in the form:
`Σ(qty × unit_price) + customization_fee − discount + delivery_fee`.

### 5.4 Status changes

- A `PENDING_REVIEW` order can only be **cancelled**, with `PATCH /admin/orders/<id>/` and
  `{"status":"CANCELLED","note":"…"}`. Any other move returns `400 "Send a quote for this custom
  order before moving it to …"`. Hide the other status actions until the order is quoted.
- After the quote, everything works as it does for regular orders: confirm (COD), processing,
  shipped, delivered, record payment, refunds, notes and bulk status.
- Custom orders never reserve stock (`stock_reserved` is always `false`).

### 5.5 Files (staff)

| Method | Endpoint | Body |
| --- | --- | --- |
| GET | `/api/v1/admin/orders/<id>/attachments/` | none |
| POST | `/api/v1/admin/orders/<id>/attachments/` | `multipart`: `file`, `kind` (default `MOCKUP`), `note`, `file_name`. Or JSON `{url, kind, note, file_name}` with a URL already in this app's Cloudinary folder. |
| DELETE | `/api/v1/admin/orders/<id>/attachments/<attachment_id>/` | none. The Cloudinary file is deleted too. |

Use this to share mockups or proofs with the customer. They appear on the tracking page.

### 5.6 Dashboard stats

`GET /api/v1/admin/stats/` adds the following under `data.orders`:

```json
{
  "by_status": { "PENDING_REVIEW": 3, "…": 0 },
  "by_order_type": { "REGULAR": 41, "CUSTOM": 6 },
  "custom_awaiting_quote": 3
}
```

`custom_awaiting_quote` is the count for all time, not just the selected date range. Use it for a
"3 custom orders need a quote" card.

---

## 6. Endpoint summary

### New

| Method | Endpoint | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/catalog/custom-templates/` | none | List templates |
| GET | `/api/v1/catalog/custom-templates/<slug\|id\|design_code>/` | none | Template detail |
| POST | `/api/v1/orders/custom/uploads/` | none | Upload a design or logo file |
| POST | `/api/v1/orders/custom/estimate/` | none | Estimated price |
| POST | `/api/v1/orders/custom/` | none | Place a custom order |
| POST | `/api/v1/orders/custom/<order_number>/attachments/` | email/phone in body | Add files later |
| POST | `/api/v1/admin/orders/<id>/quote/` | staff | Send or update the quote |
| GET/POST | `/api/v1/admin/orders/<id>/attachments/` | staff | List or add files |
| DELETE | `/api/v1/admin/orders/<id>/attachments/<attachment_id>/` | staff | Remove a file |

### Changed

| Endpoint | Change |
| --- | --- |
| `GET /catalog/products/` | `kind` filter (default `REGULAR`). Search matches `design_code`. Cards include `kind`, `design_code`, `min_order_quantity`. |
| `GET /catalog/products/<slug>/` | Returns templates as well. New detail fields. |
| `GET /catalog/collections/` | `custom_template_count`. `product_count` counts regular products only. New `?has_custom_templates=true` filter. |
| `GET /catalog/collections/<slug>/` | New `custom_templates[]`. `products[]` holds regular products only. |
| `GET /orders/track/`, `POST /orders/` | New order and item fields (§3.6). |
| `GET /admin/catalog/products/` | `kind` filter. Search matches `design_code`. Writable template fields. |
| `GET /admin/catalog/collections/` | `custom_template_count`. |
| `GET /admin/orders/` | `order_type` filter. Rows add `order_type`, `total_quantity`, `needed_by`, `quoted_at`. Search matches design codes. |
| `GET /admin/orders/<id>/` | `attachments[]` and the custom fields. |
| `GET /admin/stats/` | `by_order_type`, `custom_awaiting_quote`, `PENDING_REVIEW` in `by_status`. |
| `POST /base/file/upload/` | Response adds `resource_type`. `key` is the Cloudinary public id. |
| `GET /base/file/`, `DELETE /base/file/delete/` | Optional `resource_type` (`image`, `raw` or `video`) when you pass `key`. Not needed when you pass `url`. `GET` returns the public delivery URL; with `download=true` it returns a forced-download URL. |

---

## 7. Implementation checklist

**Storefront**
- [ ] Allow `res.cloudinary.com` images, and optionally add the `cld()` transform helper
- [ ] Add `XXXL` with the label "3XL"
- [ ] Custom templates listing page, a collection-page section and a design-code search
- [ ] Template detail page with size chart, colours, minimum quantity and production days
- [ ] Builder: file uploader (from the browser), line editor (size × colour × qty × note), instructions,
      size guidelines, needed-by date, live estimate, submit
- [ ] Tracking page: custom status labels, quote breakdown, Pay button gated on
      `requires_online_payment`, attachments, an "Add files" action, and the 72 h payment notice

**Admin panel**
- [ ] Product form: `kind` toggle, template fields, variants hidden for CUSTOM
- [ ] Products list: `kind` filter or tabs
- [ ] Orders: "Custom" tab (`order_type=CUSTOM`), new columns, `PENDING_REVIEW` badge
- [ ] Order detail: brief, lines with notes, files (upload, delete), quote form with live total
- [ ] Status actions: only "Cancel" before the quote
- [ ] Dashboard card for `custom_awaiting_quote`
