# Customer reviews: frontend integration guide

Reviews are **store-wide**: customers rate the shop overall, not a single product. Each review can
optionally reference a product (`product_id`), so per-product reviews can be added later without
changing the API.

Reviews go through moderation:

```
Customer submits ──► PENDING ──(staff approve)──► APPROVED (shown on the site)
                          └────(staff hide)─────► HIDDEN   (never shown)
Staff-created reviews are APPROVED immediately unless staff choose otherwise.
```

All responses use the usual envelope `{status, success, message, data, meta?, errors?}`.

---

## 1. Storefront

### 1.1 Show reviews

`GET /api/v1/reviews/` returns only **approved** reviews. It's public and paginated.

| Query param | Example | Meaning |
| --- | --- | --- |
| `featured` | `true` | Only featured reviews. Use this for the homepage carousel. |
| `rating` | `5` or `4,5` | Exact star ratings. |
| `min_rating` | `4` | This many stars or more. |
| `verified` | `true` | Only reviews linked to a real order. |
| `product` | slug or id | Reviews that reference this product (for later use). |
| `ordering` | `newest` (default), `oldest`, `highest`, `lowest` | Sort order. |
| `page`, `page_size` | `1`, `12` | Pagination (max page size 100). |

```json
{
  "data": [
    {
      "id": "…",
      "customer_name": "Tanvir Hasan",
      "customer_title": "CSE '26, University of Dhaka",
      "rating": 5,
      "title": "Perfect batch jerseys",
      "comment": "Ordered 40 jerseys for our tournament…",
      "is_featured": true,
      "is_verified_purchase": false,
      "product": null,
      "published_at": "2026-11-18T10:00:00Z",
      "created_at": "2026-11-18T09:40:00Z"
    }
  ],
  "meta": { "count": 4, "page": 1, "page_size": 20, "num_pages": 1, "next": null, "previous": null }
}
```

`product`, when set, looks like `{ "id", "name", "slug", "kind" }`. Customer email and phone are
never returned by public endpoints.

### 1.2 Rating summary

`GET /api/v1/reviews/summary/` accepts the same filters as the list.

```json
{ "data": { "count": 4, "average_rating": 4.8, "distribution": { "5": 3, "4": 1, "3": 0, "2": 0, "1": 0 } } }
```

`average_rating` is `null` when there are no approved reviews. Hide the stars widget in that case.

### 1.3 Submit a review

`POST /api/v1/reviews/` needs no auth.

```json
{
  "customer_name": "Tanvir Hasan",
  "customer_title": "CSE '26, University of Dhaka",
  "customer_email": "tanvir@example.com",
  "customer_phone": "",
  "rating": 5,
  "title": "Perfect batch jerseys",
  "comment": "Ordered 40 jerseys for our tournament…",
  "order_number": "ARS-261118-ABC123",
  "product_id": null
}
```

| Field | Rules |
| --- | --- |
| `customer_name` | Required, up to 120 characters. |
| `rating` | Required, 1 to 5. |
| `comment` | Required, 5 to 2000 characters. |
| `customer_title`, `title` | Optional, up to 120 and 150 characters. Shown under the name and as a heading. |
| `customer_email`, `customer_phone` | Optional and private: only staff see them. One of them is required when `order_number` is sent. |
| `order_number` | Optional. It must match a non-cancelled order placed with the same email or phone. That marks the review as a **verified purchase**. Each order can be reviewed once. |
| `product_id` | Optional, an active product id. Not needed yet. |

The response is `201`:

```json
{ "message": "Thank you! Your review will appear on the site once it is approved.",
  "data": { "id": "…", "status": "PENDING", "is_verified_purchase": true } }
```

Show `message` as a confirmation. Don't add the review to the visible list, because it isn't
published yet.

Errors return `400` with `errors.<field>`, for example `errors.order_number: ["No order found for
that number and email/phone."]`.

The rate limit is 5 submissions per hour per IP by default (`429` when exceeded). Call this endpoint
from the **browser**, not through the Next.js server. Otherwise every customer shares the server's
IP for the rate limit.

**Suggested placement:** offer a "Rate your order" form on the order tracking page once the order
is `DELIVERED`. Pre-fill `order_number` and the contact details there, so the review is verified
automatically.

### 1.4 Caching

When the storefront revalidation webhook is configured, approving, hiding, editing or deleting a
review sends the cache tag **`reviews`**. Tag the review list and summary fetches with it.

---

## 2. Admin panel

All admin endpoints need a staff JWT.

### 2.1 List

`GET /api/v1/admin/reviews/` returns reviews in every status.

It takes all the storefront filters, plus:
- `status` (`PENDING`, `APPROVED`, `HIDDEN`; comma-separated values allowed)
- `source` (`CUSTOMER`, `STAFF`)
- `search` (name, email, phone, title, comment, or an exact order number)
- `ordering`, which also accepts `created_at` and `-created_at` (the default)

`meta.status_counts` holds `{ "PENDING": 3, "APPROVED": 12, "HIDDEN": 1 }`, counted across all
reviews. Use it for tab badges such as "Pending (3)".

Each row contains the public fields plus `customer_email`, `customer_phone`, `status`, `source`,
`order_number`, `moderated_by` (`{id, name, email}`), `moderated_at` and `updated_at`.

### 2.2 Add a review as staff

`POST /api/v1/admin/reviews/`

The body has the same fields as the customer form. Use `product_id` for the product reference
(`order_number` isn't accepted here). You can also send:
- `status`, which defaults to `APPROVED`
- `is_featured`

Use this to add testimonials collected offline, for example on Facebook or WhatsApp. These reviews
get `source: "STAFF"`.

### 2.3 Moderate or edit one review

`PATCH /api/v1/admin/reviews/<id>/`

```json
{ "status": "APPROVED", "is_featured": true }
```

- **Approve:** `{"status": "APPROVED"}`. The first approval sets `published_at`.
- **Hide:** `{"status": "HIDDEN"}`. The review disappears from the site immediately.
- **Feature:** `{"is_featured": true}`. A featured review only shows on the site while it is
  approved.
- Any text field can be edited too, for example to fix a typo or remove personal details.
- `DELETE /api/v1/admin/reviews/<id>/` removes the review permanently.

### 2.4 Bulk moderation

`POST /api/v1/admin/reviews/bulk/`

```json
{ "ids": ["…", "…"], "action": "approve" }
```

`action` is one of `approve`, `hide`, `pending`, `feature`, `unfeature` or `delete`. The response
is `{ "action", "affected" }`.

---

## 3. Checklist

**Storefront**
- [ ] Reviews section or page: summary stars and distribution bars, list, filters and pagination
- [ ] Homepage carousel using `?featured=true`
- [ ] "Write a review" form, with a pending-approval confirmation
- [ ] "Rate your order" form on delivered orders, pre-filled with the order number
- [ ] Badges for `is_verified_purchase` and `customer_title`

**Admin panel**
- [ ] Reviews page with Pending, Approved and Hidden tabs (counts from `meta.status_counts`)
- [ ] Row actions: approve, hide, feature, edit, delete. Checkbox bulk actions.
- [ ] "Add review" form for staff testimonials
