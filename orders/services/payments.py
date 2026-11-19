from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from orders.models import Payment
from orders.services import bkash
from orders.services.orders import (
    PENDING_STATUSES,
    OutOfStockError,
    add_order_note,
    confirm_order,
    get_payment_due,
    mark_order_paid_amount,
)
from orders.utils.choices import (
    OrderStatus,
    PaymentMethod,
    PaymentPlan,
    PaymentStatus,
)


class PaymentError(ValueError):
    pass


def initiate_bkash_payment(order, *, callback_url=None):
    """Start a bKash tokenized checkout for the amount due under the order's plan."""
    from django.conf import settings

    if order.status == OrderStatus.CANCELLED:
        raise PaymentError("This order has been cancelled.")
    if order.payment_plan == PaymentPlan.COD:
        raise PaymentError("Cash on delivery orders are paid on delivery.")
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise PaymentError("This order has already been processed.")

    due = get_payment_due(order)
    if due <= Decimal("0.00"):
        raise PaymentError("Nothing left to pay for this order.")

    callback_url = callback_url or settings.BKASH_CALLBACK_URL
    try:
        bkash_payload = bkash.create_payment(
            amount=f"{due.normalize():f}",
            invoice_number=order.order_number,
            callback_url=callback_url,
            customer_phone=order.customer_phone,
        )
    except bkash.BkashError as exc:
        raise PaymentError(str(exc)) from exc

    payment = Payment.objects.create(
        order=order,
        method=PaymentMethod.BKASH,
        plan=order.payment_plan,
        amount=due,
        status=PaymentStatus.INITIATED,
        bkash_payment_id=bkash_payload["paymentID"],
        metadata={"create_response": bkash_payload},
    )
    return {
        "payment_id": str(payment.pk),
        "bkash_payment_id": payment.bkash_payment_id,
        "amount": str(due),
        "payment_url": bkash_payload["bkashURL"],
    }


@transaction.atomic
def settle_bkash_payment(*, bkash_payment_id, trx_id="", metadata=None):
    """Mark a bKash payment completed, update the order and confirm it."""
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .select_related("order")
        .filter(bkash_payment_id=bkash_payment_id)
        .first()
    )
    if payment is None:
        raise PaymentError("Unknown bKash payment.")
    if payment.status == PaymentStatus.COMPLETED:
        return payment

    order = payment.order
    payment.status = PaymentStatus.COMPLETED
    payment.bkash_trx_id = trx_id
    payment.completed_at = timezone.now()
    if metadata:
        payment.metadata.update(metadata)
    payment.save(
        update_fields=[
            "status",
            "bkash_trx_id",
            "completed_at",
            "metadata",
            "updated_at",
        ]
    )

    mark_order_paid_amount(order, payment.amount)
    if order.status == OrderStatus.CANCELLED:
        add_order_note(
            order,
            f"bKash payment of {payment.amount} BDT arrived after the order was "
            "cancelled. Refund required.",
        )
    elif order.status in PENDING_STATUSES:
        note = (
            "bKash payment received"
            f" ({payment.amount} BDT, plan {payment.get_plan_display()})."
        )
        try:
            # The savepoint keeps the payment recorded even if confirming fails.
            with transaction.atomic():
                confirm_order(order, note=note)
        except OutOfStockError as exc:
            # Only possible for orders placed before stock was reserved at
            # checkout. The customer has paid, so never fail the callback:
            # leave the order pending for staff to resolve.
            order.refresh_from_db()
            add_order_note(
                order,
                f"{note} Could not confirm automatically: {exc} "
                "Restock or cancel and refund.",
            )
    return payment


@transaction.atomic
def fail_bkash_payment(*, bkash_payment_id, status, reason=""):
    payment = (
        Payment.objects.select_related("order")
        .filter(bkash_payment_id=bkash_payment_id)
        .first()
    )
    if payment is None or payment.status == PaymentStatus.COMPLETED:
        return payment

    payment.status = status
    payment.failure_reason = reason or payment.failure_reason
    payment.save(update_fields=["status", "failure_reason", "updated_at"])
    return payment


def execute_bkash_payment(bkash_payment_id):
    """Execute the bKash capture after the customer redirected back with success."""
    try:
        payload = bkash.execute_payment(bkash_payment_id)
    except bkash.BkashError as exc:
        raise PaymentError(str(exc)) from exc

    if str(payload.get("transactionStatus", "")).lower() != "completed":
        raise PaymentError(
            payload.get("statusMessage")
            or "bKash did not complete this transaction."
        )

    return settle_bkash_payment(
        bkash_payment_id=bkash_payment_id,
        trx_id=payload.get("trxID", ""),
        metadata={"execute_response": payload},
    )
