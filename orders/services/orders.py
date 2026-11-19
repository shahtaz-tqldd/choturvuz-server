import secrets
from collections import deque
from decimal import Decimal
from string import ascii_uppercase, digits

from django.db import transaction
from django.utils import timezone

from app.utils.phone import normalize_phone
from base.models import StoreConfig
from catalog.models import ProductVariant
from catalog.services.inventory import InsufficientStockError, apply_stock_changes
from catalog.utils.choices import StockMovementReason
from orders.models import Order, OrderItem, OrderStatusEvent, Refund
from orders.utils.choices import (
    OrderEventKind,
    OrderPaymentStatus,
    OrderStatus,
    OrderType,
    PaymentPlan,
    RefundMethod,
)

ORDER_NUMBER_ALPHABET = ascii_uppercase + digits

PENDING_STATUSES = (OrderStatus.PENDING_PAYMENT, OrderStatus.PENDING_CONFIRMATION)


def generate_order_number():
    stamp = timezone.localdate().strftime("%y%m%d")
    while True:
        suffix = "".join(secrets.choice(ORDER_NUMBER_ALPHABET) for _ in range(6))
        order_number = f"ARS-{stamp}-{suffix}"
        if not Order.objects.filter(order_number=order_number).exists():
            return order_number


DEFAULT_FEE_INSIDE_DHAKA = Decimal("70.00")
DEFAULT_FEE_OUTSIDE_DHAKA = Decimal("130.00")


def get_delivery_fee(city, config=None, subtotal=None):
    """Delivery fee inside Dhaka vs outside, from the store config.

    Free when `subtotal` reaches the configured free-delivery threshold.
    """
    config = config or StoreConfig.objects.first()
    threshold = getattr(config, "free_delivery_threshold", None) if config else None
    if threshold and subtotal is not None and subtotal >= threshold:
        return Decimal("0.00")

    inside = config.delivery_fee_inside_dhaka if config else DEFAULT_FEE_INSIDE_DHAKA
    outside = config.delivery_fee_outside_dhaka if config else DEFAULT_FEE_OUTSIDE_DHAKA
    fee = inside if (city or "").strip().lower() == "dhaka" else outside
    return Decimal(fee).quantize(Decimal("0.01"))


def advance_for(total, payment_plan):
    """Amount payable online before the order ships."""
    if payment_plan == PaymentPlan.COD:
        return Decimal("0.00")
    if payment_plan == PaymentPlan.HALF:
        return (total / Decimal("2")).quantize(Decimal("0.01"))
    return total


class OutOfStockError(ValueError):
    pass


class PaymentPlanError(ValueError):
    pass


class OrderUpdateError(ValueError):
    pass


def check_payment_plan(payment_plan, config=None):
    config = config or StoreConfig.objects.first()
    if payment_plan == PaymentPlan.HALF and config and not config.allow_half_payment:
        raise PaymentPlanError("Half payment is currently disabled.")
    if payment_plan == PaymentPlan.COD and config and not config.allow_cod:
        raise PaymentPlanError("Cash on delivery is currently disabled.")


def _active_variants(variant_ids):
    return {
        str(variant.pk): variant
        for variant in ProductVariant.objects.select_related("product").filter(
            pk__in=list(variant_ids),
            is_active=True,
            product__status="ACTIVE",
            product__kind="REGULAR",
        )
    }


def quote_order(*, items_data, city="", payment_plan=PaymentPlan.FULL):
    """Price a cart without placing an order (read-only, no locks).

    Unavailable lines (inactive, sold out or short on stock) are flagged and
    left out of the totals.
    """
    config = StoreConfig.objects.first()
    variants = _active_variants(str(item["variant_id"]) for item in items_data)

    lines = []
    subtotal = Decimal("0.00")
    for item in items_data:
        key = str(item["variant_id"])
        quantity = int(item["quantity"])
        variant = variants.get(key)
        if variant is None:
            lines.append(
                {
                    "variant_id": key,
                    "quantity": quantity,
                    "unit_price": None,
                    "line_total": None,
                    "stock": 0,
                    "available": False,
                    "reason": "unavailable",
                }
            )
            continue

        unit_price = variant.effective_price
        available = variant.stock >= quantity
        line_total = unit_price * quantity
        if available:
            subtotal += line_total
        lines.append(
            {
                "variant_id": key,
                "product_id": str(variant.product_id),
                "product_name": variant.product.name,
                "product_slug": variant.product.slug,
                "variant_name": variant.display_name,
                "sku": variant.sku,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "stock": variant.stock,
                "available": available,
                "reason": None if available else (
                    "sold_out" if variant.stock == 0 else "insufficient_stock"
                ),
            }
        )

    delivery_fee = get_delivery_fee(city, config=config, subtotal=subtotal)
    total = subtotal + delivery_fee
    threshold = getattr(config, "free_delivery_threshold", None) if config else None
    return {
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total": total,
        "advance_amount": advance_for(total, payment_plan),
        "payment_plan": payment_plan,
        "free_delivery_threshold": threshold,
        "all_available": all(line["available"] for line in lines),
        "items": lines,
    }


def log_order_event(order, note="", *, kind=OrderEventKind.STATUS, status=None, created_by=None):
    return OrderStatusEvent.objects.create(
        order=order,
        status=status or order.status,
        kind=kind,
        note=note,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )


def _lock(order):
    """Lock the order row and reload it so concurrent updates serialize."""
    list(Order.objects.select_for_update().filter(pk=order.pk).values_list("pk", flat=True))
    order.refresh_from_db()
    return order


@transaction.atomic
def create_order(
    *, customer_data, items_data, payment_plan=PaymentPlan.FULL, placed_by=None
):
    """Create a guest order with server-priced items and a delivery fee.

    Stock is reserved immediately (rows locked, checked, decremented) so two
    customers can never both buy the last item. It is given back when the
    order is cancelled or expires unpaid.

    `items_data` is a list of dicts with `variant_id` and `quantity`.
    """
    config = StoreConfig.objects.first()
    check_payment_plan(payment_plan, config)

    customer_data = dict(customer_data)
    customer_data["customer_phone"] = normalize_phone(customer_data.get("customer_phone", ""))

    variants = _active_variants(str(item["variant_id"]) for item in items_data)
    missing = [str(item["variant_id"]) for item in items_data if str(item["variant_id"]) not in variants]
    if missing:
        raise OutOfStockError("One or more items in your cart are no longer available.")

    is_cod = payment_plan == PaymentPlan.COD
    order = Order(
        order_number=generate_order_number(),
        payment_plan=payment_plan,
        status=OrderStatus.PENDING_CONFIRMATION if is_cod else OrderStatus.PENDING_PAYMENT,
        placed_by=placed_by,
        **customer_data,
    )

    subtotal = Decimal("0.00")
    lines = []
    for item in items_data:
        variant = variants[str(item["variant_id"])]
        quantity = int(item["quantity"])
        unit_price = variant.effective_price
        line_total = unit_price * quantity
        subtotal += line_total
        lines.append((variant, quantity, unit_price, line_total))

    order.subtotal = subtotal
    order.delivery_fee = get_delivery_fee(order.city, config=config, subtotal=subtotal)
    order.total = subtotal + order.delivery_fee
    order.save()

    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                product=variant.product,
                variant=variant,
                product_name=variant.product.name,
                variant_name=variant.display_name,
                sku=variant.sku,
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
            )
            for variant, quantity, unit_price, line_total in lines
        ]
    )

    try:
        apply_stock_changes(
            {variant.pk: -quantity for variant, quantity, _, _ in lines},
            reason=StockMovementReason.ORDER_PLACED,
            order=order,
            note=order.order_number,
        )
    except InsufficientStockError as exc:
        # Raising rolls back the whole order.
        raise OutOfStockError(str(exc)) from exc

    order.stock_reserved = True
    order.save(update_fields=["stock_reserved", "updated_at"])

    log_order_event(
        order,
        "Order placed with cash on delivery. Awaiting confirmation."
        if is_cod
        else "Order placed. Awaiting payment.",
    )
    return order


def get_payment_due(order):
    """Amount payable online now, based on the selected plan."""
    due = advance_for(order.total, order.payment_plan) - order.paid_amount
    return max(due, Decimal("0.00"))


@transaction.atomic
def mark_order_paid_amount(order, amount):
    """Add a successful payment and recalculate the payment status."""
    _lock(order)
    order.paid_amount = (order.paid_amount or Decimal("0.00")) + Decimal(str(amount))
    if order.status == OrderStatus.CANCELLED:
        # Money arrived after cancellation; it has to go back.
        order.payment_status = (
            OrderPaymentStatus.REFUNDED
            if order.refunded_amount >= order.paid_amount
            else OrderPaymentStatus.REFUND_PENDING
        )
    elif order.paid_amount >= order.total:
        order.payment_status = OrderPaymentStatus.PAID
    else:
        order.payment_status = OrderPaymentStatus.PARTIALLY_PAID
    order.save(update_fields=["paid_amount", "payment_status", "updated_at"])


def reserve_stock(order, user=None):
    """Deduct the order's quantities from stock, once.

    Custom orders are made to order and never hold stock.
    """
    if order.stock_reserved or order.order_type == OrderType.CUSTOM:
        return
    changes = {}
    for item in order.items.all():
        if item.variant_id is not None:
            changes[item.variant_id] = changes.get(item.variant_id, 0) - item.quantity
    try:
        apply_stock_changes(
            changes,
            reason=StockMovementReason.ORDER_PLACED,
            order=order,
            user=user,
            note=order.order_number,
        )
    except InsufficientStockError as exc:
        raise OutOfStockError(str(exc)) from exc
    order.stock_reserved = True
    order.save(update_fields=["stock_reserved", "updated_at"])


def release_stock(order, user=None):
    """Return reserved stock, once. Orders that never reserved return nothing."""
    if not order.stock_reserved:
        return
    changes = {}
    for item in order.items.all():
        if item.variant_id is not None:
            changes[item.variant_id] = changes.get(item.variant_id, 0) + item.quantity
    apply_stock_changes(
        changes,
        reason=StockMovementReason.ORDER_CANCELLED,
        order=order,
        user=user,
        note=order.order_number,
    )
    order.stock_reserved = False
    order.save(update_fields=["stock_reserved", "updated_at"])


@transaction.atomic
def confirm_order(order, note="Payment received. Order confirmed.", created_by=None):
    """Confirm a pending order, making sure its stock is reserved.

    New orders already hold their stock. Orders placed before reservation at
    checkout existed are reserved here, under a row lock; if the stock is gone
    OutOfStockError is raised and nothing changes.
    """
    _lock(order)
    if order.status not in PENDING_STATUSES:
        return order

    reserve_stock(order, user=created_by)
    order.status = OrderStatus.CONFIRMED
    order.save(update_fields=["status", "updated_at"])
    log_order_event(order, note, created_by=created_by)
    return order


@transaction.atomic
def cancel_order(order, reason="", created_by=None):
    """Cancel an order and return any stock it holds."""
    _lock(order)
    if order.status == OrderStatus.CANCELLED:
        return order

    release_stock(order, user=created_by)

    order.status = OrderStatus.CANCELLED
    order.cancelled_reason = reason
    if order.paid_amount > order.refunded_amount:
        order.payment_status = OrderPaymentStatus.REFUND_PENDING
    order.save(
        update_fields=["status", "cancelled_reason", "payment_status", "updated_at"]
    )
    log_order_event(order, reason or "Order cancelled.", created_by=created_by)
    return order


ALLOWED_STATUS_TRANSITIONS = {
    # Custom orders leave review only through a quote (custom_orders.quote_custom_order).
    OrderStatus.PENDING_REVIEW: {OrderStatus.CANCELLED},
    OrderStatus.PENDING_PAYMENT: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.PENDING_CONFIRMATION: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


def _status_label(value):
    return dict(OrderStatus.choices).get(value, value)


@transaction.atomic
def transition_order_status(
    order,
    new_status,
    note="",
    created_by=None,
    courier=None,
    tracking_number=None,
):
    if new_status == order.status:
        return order

    if order.status == OrderStatus.PENDING_REVIEW and new_status != OrderStatus.CANCELLED:
        raise ValueError(
            "Send a quote for this custom order before moving it to "
            f"{_status_label(new_status)}."
        )

    allowed = ALLOWED_STATUS_TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot move order from {_status_label(order.status)} to "
            f"{_status_label(new_status)}."
        )

    if new_status == OrderStatus.CANCELLED:
        return cancel_order(order, reason=note, created_by=created_by)

    if new_status == OrderStatus.CONFIRMED:
        return confirm_order(order, note=note or "Order confirmed.", created_by=created_by)

    _lock(order)
    if new_status not in ALLOWED_STATUS_TRANSITIONS.get(order.status, set()):
        raise ValueError(
            f"Cannot move order from {_status_label(order.status)} to "
            f"{_status_label(new_status)}."
        )

    order.status = new_status
    update_fields = ["status", "updated_at"]
    if new_status == OrderStatus.SHIPPED:
        order.shipped_at = timezone.now()
        update_fields.append("shipped_at")
        if courier is not None:
            order.courier = courier
            update_fields.append("courier")
        if tracking_number is not None:
            order.tracking_number = tracking_number
            update_fields.append("tracking_number")
    elif new_status == OrderStatus.DELIVERED:
        order.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    order.save(update_fields=update_fields)

    log_order_event(order, note, created_by=created_by)
    return order


def status_path(current, target):
    """Shortest list of statuses leading from `current` to `target`.

    CANCELLED is never used as a stepping stone. Returns None when `target`
    cannot be reached.
    """
    if current == target:
        return []
    queue = deque([(current, [])])
    seen = {current}
    while queue:
        status, path = queue.popleft()
        for nxt in sorted(ALLOWED_STATUS_TRANSITIONS.get(status, set())):
            if nxt in seen:
                continue
            if nxt == target:
                return path + [nxt]
            if nxt == OrderStatus.CANCELLED:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [nxt]))
    return None


@transaction.atomic
def advance_order_status(order, target, note="", created_by=None, **shipping):
    """Move an order to `target`, stepping through intermediate statuses."""
    path = status_path(order.status, target)
    if path is None:
        raise ValueError(
            f"Cannot move order from {_status_label(order.status)} to "
            f"{_status_label(target)}."
        )
    for index, step in enumerate(path):
        is_last = index == len(path) - 1
        transition_order_status(
            order,
            step,
            note=note if is_last else f"Moved to {_status_label(step)} on the way to {_status_label(target)}.",
            created_by=created_by,
            **(shipping if step == OrderStatus.SHIPPED else {}),
        )
    return order


CUSTOMER_FIELDS = (
    "customer_name",
    "customer_email",
    "customer_phone",
    "address_line",
    "city",
    "postal_code",
)
LOCKED_FOR_EDIT = (OrderStatus.DELIVERED, OrderStatus.CANCELLED)


@transaction.atomic
def update_order_details(order, data, *, created_by=None, recalculate_delivery_fee=True):
    """Fix customer/shipping details (and courier info) after the order was placed."""
    _lock(order)
    customer_changes = {
        field: data[field]
        for field in CUSTOMER_FIELDS
        if field in data and data[field] != getattr(order, field)
    }
    if customer_changes and order.status in LOCKED_FOR_EDIT:
        raise OrderUpdateError(
            f"Customer details cannot be changed on a {_status_label(order.status).lower()} order."
        )
    if "customer_phone" in customer_changes:
        customer_changes["customer_phone"] = normalize_phone(customer_changes["customer_phone"])
    if "customer_email" in customer_changes:
        customer_changes["customer_email"] = (customer_changes["customer_email"] or "").strip().lower()

    shipping_changes = {
        field: data[field]
        for field in ("courier", "tracking_number")
        if field in data and data[field] != getattr(order, field)
    }

    changes = {**customer_changes, **shipping_changes}
    if not changes:
        return order

    for field, value in changes.items():
        setattr(order, field, value)
    update_fields = list(changes) + ["updated_at"]

    notes = [f"Updated {', '.join(field.replace('_', ' ') for field in changes)}."]
    if "city" in customer_changes and recalculate_delivery_fee:
        new_fee = get_delivery_fee(order.city, subtotal=order.subtotal)
        if new_fee != order.delivery_fee:
            notes.append(f"Delivery fee {order.delivery_fee} → {new_fee} BDT.")
            order.delivery_fee = new_fee
            order.total = order.compute_total()
            update_fields += ["delivery_fee", "total"]

    if order.payment_status in (OrderPaymentStatus.PAID, OrderPaymentStatus.PARTIALLY_PAID):
        order.payment_status = (
            OrderPaymentStatus.PAID
            if order.paid_amount >= order.total
            else OrderPaymentStatus.PARTIALLY_PAID
        )
        update_fields.append("payment_status")

    order.save(update_fields=list(dict.fromkeys(update_fields)))
    log_order_event(order, " ".join(notes), kind=OrderEventKind.EDIT, created_by=created_by)
    return order


def add_order_note(order, note, *, created_by=None):
    """Internal timeline note; never shown on the public tracking page."""
    return log_order_event(order, note, kind=OrderEventKind.NOTE, created_by=created_by)


class RefundError(ValueError):
    pass


@transaction.atomic
def record_refund(
    order,
    *,
    amount,
    method,
    trx_id="",
    note="",
    created_by=None,
    via_bkash=False,
):
    """Record (and optionally execute through bKash) a refund for an order."""
    from orders.services import bkash
    from orders.models import Payment
    from orders.utils.choices import PaymentMethod, PaymentStatus

    _lock(order)
    if order.status != OrderStatus.CANCELLED:
        raise RefundError("Refunds can only be recorded for cancelled orders.")

    refundable = order.paid_amount - order.refunded_amount
    if refundable <= 0:
        raise RefundError("Nothing left to refund on this order.")
    amount = Decimal(str(amount))
    if amount > refundable:
        raise RefundError(f"Amount exceeds the refundable balance of {refundable} BDT.")

    payment = None
    metadata = {}
    if via_bkash:
        if method != RefundMethod.BKASH:
            raise RefundError("Automatic refunds are only available through bKash.")
        payment = (
            Payment.objects.filter(
                order=order,
                method=PaymentMethod.BKASH,
                status=PaymentStatus.COMPLETED,
                amount__gte=amount,
            )
            .exclude(bkash_trx_id="")
            .order_by("-completed_at")
            .first()
        )
        if payment is None:
            raise RefundError("No completed bKash payment covers this refund amount.")
        try:
            payload = bkash.refund_payment(
                payment_id=payment.bkash_payment_id,
                trx_id=payment.bkash_trx_id,
                amount=f"{amount.normalize():f}",
                reason=(note or "Order cancelled")[:255],
                sku=order.order_number,
            )
        except bkash.BkashError as exc:
            raise RefundError(str(exc)) from exc
        trx_id = payload.get("refundTrxID") or trx_id
        metadata["bkash_response"] = payload

    refund = Refund.objects.create(
        order=order,
        payment=payment,
        amount=amount,
        method=method,
        trx_id=trx_id or "",
        note=note,
        metadata=metadata,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )

    order.refunded_amount += amount
    order.payment_status = (
        OrderPaymentStatus.REFUNDED
        if order.refunded_amount >= order.paid_amount
        else OrderPaymentStatus.REFUND_PENDING
    )
    order.save(update_fields=["refunded_amount", "payment_status", "updated_at"])

    method_label = dict(RefundMethod.choices).get(method, method)
    message = f"Refund of {amount} BDT via {method_label}"
    if refund.trx_id:
        message += f" (trx {refund.trx_id})"
    message += "."
    if note:
        message += f" {note}"
    log_order_event(order, message, kind=OrderEventKind.REFUND, created_by=created_by)
    return refund
