from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from orders.models import Order, Payment
from orders.services.orders import cancel_order
from orders.utils.choices import (
    BKASH_PAYMENT_PLANS,
    OrderStatus,
    OrderType,
    PaymentStatus,
)

# Don't expire an order while its customer may still be on the bKash page.
ACTIVE_PAYMENT_GRACE_MINUTES = 30


def stale_pending_hours():
    return getattr(settings, "PENDING_PAYMENT_EXPIRY_HOURS", 24)


def cancel_stale_pending_orders(now=None):
    """Cancel unpaid bKash orders past the cutoff and return their stock.

    Cash-on-delivery orders (PENDING_CONFIRMATION) are left for staff.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(hours=stale_pending_hours())
    recent_payment_cutoff = now - timedelta(minutes=ACTIVE_PAYMENT_GRACE_MINUTES)
    busy_orders = Payment.objects.filter(
        status__in=(PaymentStatus.INITIATED, PaymentStatus.PENDING),
        created_at__gte=recent_payment_cutoff,
    ).values("order_id")
    # Regular orders expire from when they were placed; custom orders from
    # when the quote was sent (they may sit in review for days).
    is_stale = Q(order_type=OrderType.REGULAR, created_at__lte=cutoff)
    quote_hours = getattr(settings, "CUSTOM_ORDER_QUOTE_EXPIRY_HOURS", 72)
    if quote_hours:
        is_stale |= Q(
            order_type=OrderType.CUSTOM,
            quoted_at__lte=now - timedelta(hours=quote_hours),
        )
    stale_orders = (
        Order.objects.filter(
            status=OrderStatus.PENDING_PAYMENT,
            payment_plan__in=BKASH_PAYMENT_PLANS,
        )
        .filter(is_stale)
        .exclude(pk__in=busy_orders)
    )

    cancelled = 0
    for order in stale_orders:
        cancel_order(
            order,
            reason="Automatically cancelled: payment was not completed in time.",
        )
        cancelled += 1
    return cancelled
