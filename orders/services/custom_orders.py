"""Custom orders: customer artwork on a staff-made design template.

Lifecycle
---------
1. The customer picks a custom template (Product kind=CUSTOM), uploads
   designs/logos, and submits sizes, colours and quantities. The order is
   created as PENDING_REVIEW with an *estimated* price (template price per
   piece + delivery). Nothing can be paid yet.
2. Staff review the brief and send a quote (final unit prices, a
   customization fee and/or discount). The order moves to PENDING_PAYMENT
   (bKash FULL/HALF) or PENDING_CONFIRMATION (COD) and from then on follows
   the regular order flow: pay -> CONFIRMED -> PROCESSING (production) ->
   SHIPPED -> DELIVERED, with cancellation and refunds as usual.

Custom orders never touch variant stock.
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.services.storage import is_in_folder, parse_url
from app.utils.phone import normalize_phone
from base.models import StoreConfig
from catalog.models import Product
from catalog.utils.choices import ProductKind, ProductSize, ProductStatus
from orders.models import Order, OrderAttachment, OrderItem
from orders.services.orders import (
    _lock,
    advance_for,
    check_payment_plan,
    generate_order_number,
    get_delivery_fee,
    log_order_event,
)
from orders.utils.choices import (
    AttachmentKind,
    AttachmentSource,
    OrderEventKind,
    OrderPaymentStatus,
    OrderStatus,
    OrderType,
    PaymentPlan,
)

MAX_LINES = 100

# A quote may be (re)sent while nothing has been paid yet.
QUOTABLE_STATUSES = (
    OrderStatus.PENDING_REVIEW,
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.PENDING_CONFIRMATION,
)
# Customers may add more files until production starts.
CUSTOMER_ATTACHABLE_STATUSES = QUOTABLE_STATUSES + (OrderStatus.CONFIRMED,)

SIZE_LABELS = {value: str(label) for value, label in ProductSize.choices}


class CustomOrderError(ValueError):
    """Invalid custom order request. `field` names the offending input."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def get_custom_template(template_id):
    template = (
        Product.objects.filter(
            pk=template_id, kind=ProductKind.CUSTOM, status=ProductStatus.ACTIVE
        ).first()
        if template_id
        else None
    )
    if template is None:
        raise CustomOrderError("This custom template is not available.", field="template_id")
    return template


def build_lines(template, lines_data):
    """Validate requested sizes/colours/quantities against the template.

    Returns normalised line dicts with prices from the template.
    """
    if not lines_data:
        raise CustomOrderError("Add at least one size/colour line.", field="items")
    if len(lines_data) > MAX_LINES:
        raise CustomOrderError(f"Send at most {MAX_LINES} lines.", field="items")

    allowed_sizes = list(template.custom_sizes or [])
    colors = {
        (color.get("name") or "").strip().casefold(): color
        for color in (template.custom_colors or [])
        if color.get("name")
    }
    max_quantity = settings.CUSTOM_ORDER_MAX_LINE_QUANTITY
    unit_price = template.effective_price

    lines = []
    for index, data in enumerate(lines_data):
        size = (data.get("size") or "").strip().upper()
        if allowed_sizes and not size:
            raise CustomOrderError(f"Line {index + 1}: choose a size.", field="items")
        if size and size not in SIZE_LABELS:
            raise CustomOrderError(f"Line {index + 1}: unknown size {size}.", field="items")
        if allowed_sizes and size not in allowed_sizes:
            raise CustomOrderError(
                f"Line {index + 1}: size {SIZE_LABELS[size]} is not offered for this design.",
                field="items",
            )

        color_name = (data.get("color_name") or "").strip()
        color_hex = (data.get("color_hex") or "").strip().lower()
        if colors:
            match = colors.get(color_name.casefold())
            if match is None:
                raise CustomOrderError(
                    f"Line {index + 1}: choose one of the offered colours.",
                    field="items",
                )
            color_name = match["name"]
            color_hex = (match.get("hex") or "").lower()

        quantity = int(data.get("quantity") or 0)
        if quantity < 1 or quantity > max_quantity:
            raise CustomOrderError(
                f"Line {index + 1}: quantity must be between 1 and {max_quantity}.",
                field="items",
            )

        lines.append(
            {
                "size": size,
                "color_name": color_name,
                "color_hex": color_hex,
                "quantity": quantity,
                "note": (data.get("note") or "").strip(),
                "unit_price": unit_price,
                "line_total": unit_price * quantity,
            }
        )

    total_quantity = sum(line["quantity"] for line in lines)
    if total_quantity < template.min_order_quantity:
        raise CustomOrderError(
            f"This design needs at least {template.min_order_quantity} pieces "
            f"(you asked for {total_quantity}).",
            field="items",
        )
    return lines


def _variant_name(line):
    size_label = SIZE_LABELS.get(line["size"], line["size"])
    return " / ".join(part for part in (line["color_name"], size_label) if part)


def estimate_custom_order(*, template, lines, city="", payment_plan=PaymentPlan.FULL):
    """Indicative price for a custom order; staff confirm it in the quote."""
    config = StoreConfig.objects.first()
    subtotal = sum((line["line_total"] for line in lines), Decimal("0.00"))
    delivery_fee = get_delivery_fee(city, config=config, subtotal=subtotal)
    total = subtotal + delivery_fee
    return {
        "template_id": str(template.pk),
        "design_code": template.design_code,
        "unit_price": template.effective_price,
        "total_quantity": sum(line["quantity"] for line in lines),
        "min_order_quantity": template.min_order_quantity,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total": total,
        "advance_amount": advance_for(total, payment_plan),
        "payment_plan": payment_plan,
        "is_estimate": True,
        "items": [
            {**line, "variant_name": _variant_name(line)} for line in lines
        ],
    }


def resolve_customer_upload(url):
    """Accept only files uploaded through the custom order upload endpoint."""
    parsed = parse_url(url)
    if parsed is None or not is_in_folder(
        parsed["public_id"], settings.CLOUDINARY_CUSTOM_ORDERS_FOLDER
    ):
        raise CustomOrderError(
            "Upload files through the custom order upload endpoint first.",
            field="attachments",
        )
    return parsed


def _attachment_rows(order, attachments_data, *, source, created_by=None):
    rows = []
    seen = set()
    for data in attachments_data:
        url = data["url"]
        if url in seen:
            continue
        seen.add(url)
        parsed = resolve_customer_upload(url)
        rows.append(
            OrderAttachment(
                order=order,
                kind=data.get("kind") or AttachmentKind.DESIGN,
                source=source,
                url=url,
                public_id=parsed["public_id"],
                resource_type=parsed["resource_type"],
                file_name=(data.get("file_name") or "")[:255],
                note=(data.get("note") or "")[:500],
                created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
            )
        )
    return rows


@transaction.atomic
def create_custom_order(
    *,
    customer_data,
    template,
    lines,
    attachments_data=(),
    custom_instructions="",
    size_guidelines="",
    needed_by=None,
    payment_plan=PaymentPlan.FULL,
    placed_by=None,
):
    """Place a custom order for review. `lines` come from build_lines()."""
    config = StoreConfig.objects.first()
    check_payment_plan(payment_plan, config)
    if len(attachments_data) > settings.CUSTOM_ORDER_MAX_ATTACHMENTS:
        raise CustomOrderError(
            f"Attach at most {settings.CUSTOM_ORDER_MAX_ATTACHMENTS} files.",
            field="attachments",
        )

    customer_data = dict(customer_data)
    customer_data["customer_phone"] = normalize_phone(customer_data.get("customer_phone", ""))

    order = Order(
        order_number=generate_order_number(),
        order_type=OrderType.CUSTOM,
        status=OrderStatus.PENDING_REVIEW,
        payment_plan=payment_plan,
        placed_by=placed_by,
        custom_instructions=custom_instructions,
        size_guidelines=size_guidelines,
        needed_by=needed_by,
        **customer_data,
    )
    order.subtotal = sum((line["line_total"] for line in lines), Decimal("0.00"))
    order.delivery_fee = get_delivery_fee(order.city, config=config, subtotal=order.subtotal)
    order.total = order.compute_total()
    order.save()

    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                product=template,
                variant=None,
                product_name=template.name,
                variant_name=_variant_name(line),
                sku=template.design_code,
                unit_price=line["unit_price"],
                quantity=line["quantity"],
                line_total=line["line_total"],
                size=line["size"],
                color_name=line["color_name"],
                color_hex=line["color_hex"],
                note=line["note"][:500],
                position=index,
            )
            for index, line in enumerate(lines)
        ]
    )
    OrderAttachment.objects.bulk_create(
        _attachment_rows(order, attachments_data, source=AttachmentSource.CUSTOMER)
    )

    log_order_event(
        order,
        "Custom order received. We will review your design and send the final price.",
    )
    return order


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


@transaction.atomic
def quote_custom_order(
    order,
    *,
    items=None,
    customization_fee=None,
    discount=None,
    delivery_fee=None,
    payment_plan=None,
    note="",
    created_by=None,
):
    """Set the final price of a custom order and open it for payment.

    `items` is an optional list of {"id", "unit_price"?, "quantity"?} to
    change individual lines. Fees left as None keep their current value
    (the delivery fee is then recalculated from the city).
    """
    _lock(order)
    if order.order_type != OrderType.CUSTOM:
        raise CustomOrderError("Only custom orders can be quoted.")
    if order.status not in QUOTABLE_STATUSES:
        raise CustomOrderError(
            f"A {order.get_status_display().lower()} order cannot be re-quoted."
        )
    if order.paid_amount > 0:
        raise CustomOrderError("This order already has payments; it cannot be re-quoted.")

    order_items = {str(item.pk): item for item in order.items.all()}
    for change in items or []:
        item = order_items.get(str(change["id"]))
        if item is None:
            raise CustomOrderError(f"Unknown order item {change['id']}.", field="items")
        if change.get("unit_price") is not None:
            item.unit_price = _money(change["unit_price"])
        if change.get("quantity") is not None:
            item.quantity = int(change["quantity"])
        item.line_total = item.unit_price * item.quantity
        item.save(update_fields=["unit_price", "quantity", "line_total", "updated_at"])

    if payment_plan is not None and payment_plan != order.payment_plan:
        check_payment_plan(payment_plan)
        order.payment_plan = payment_plan

    order.subtotal = sum(
        (item.line_total for item in order_items.values()), Decimal("0.00")
    )
    if customization_fee is not None:
        order.customization_fee = _money(customization_fee)
    if discount is not None:
        order.discount = _money(discount)
    order.delivery_fee = (
        _money(delivery_fee)
        if delivery_fee is not None
        else get_delivery_fee(order.city, subtotal=order.subtotal)
    )
    order.total = order.compute_total()
    if order.total <= 0:
        raise CustomOrderError("The quoted total must be greater than zero.")

    is_requote = order.quoted_at is not None
    order.status = (
        OrderStatus.PENDING_CONFIRMATION
        if order.payment_plan == PaymentPlan.COD
        else OrderStatus.PENDING_PAYMENT
    )
    order.payment_status = OrderPaymentStatus.UNPAID
    order.quoted_at = timezone.now()
    order.quote_note = note
    order.save(
        update_fields=[
            "subtotal",
            "customization_fee",
            "discount",
            "delivery_fee",
            "total",
            "payment_plan",
            "status",
            "payment_status",
            "quoted_at",
            "quote_note",
            "updated_at",
        ]
    )

    message = (
        f"{'Updated quote' if is_requote else 'Quote'}: {order.total} BDT"
        f" ({order.get_payment_plan_display()})."
    )
    if order.payment_plan == PaymentPlan.COD:
        message += " We will call you to confirm the order."
    else:
        message += f" Pay {advance_for(order.total, order.payment_plan)} BDT to confirm."
    if note:
        message += f" {note}"
    log_order_event(order, message, created_by=created_by)
    return order


@transaction.atomic
def add_customer_attachments(order, attachments_data):
    """Customer adds more files to an existing custom order."""
    _lock(order)
    if order.order_type != OrderType.CUSTOM:
        raise CustomOrderError("Files can only be added to custom orders.")
    if order.status not in CUSTOMER_ATTACHABLE_STATUSES:
        raise CustomOrderError("Files can no longer be added to this order.")
    existing = order.attachments.count()
    if existing + len(attachments_data) > settings.CUSTOM_ORDER_MAX_ATTACHMENTS:
        raise CustomOrderError(
            f"An order can have at most {settings.CUSTOM_ORDER_MAX_ATTACHMENTS} files.",
            field="attachments",
        )
    known = set(order.attachments.values_list("url", flat=True))
    rows = [
        row
        for row in _attachment_rows(order, attachments_data, source=AttachmentSource.CUSTOMER)
        if row.url not in known
    ]
    created = OrderAttachment.objects.bulk_create(rows)
    if created:
        log_order_event(
            order,
            f"Customer added {len(created)} file(s).",
            kind=OrderEventKind.EDIT,
        )
    return created


def add_staff_attachment(order, *, url, public_id, resource_type, kind, file_name="", note="", created_by=None):
    attachment = OrderAttachment.objects.create(
        order=order,
        kind=kind,
        source=AttachmentSource.STAFF,
        url=url,
        public_id=public_id,
        resource_type=resource_type,
        file_name=file_name[:255],
        note=note[:500],
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )
    label = str(dict(AttachmentKind.choices).get(kind, kind)).lower()
    log_order_event(
        order,
        f"Staff shared a {label}{f': {note}' if note else '.'}",
        kind=OrderEventKind.EDIT,
        created_by=created_by,
    )
    return attachment
