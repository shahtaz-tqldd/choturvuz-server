"""Backfill data for the stock-reservation, refund and COD changes.

* stock_reserved: before this release stock was only deducted when an order
  was confirmed through the bKash callback. Admin-confirmed orders never
  deducted stock (bug A1), so only orders with a completed bKash payment are
  marked as holding stock. Cancelling the others later will not add stock that
  was never taken.
* REFUNDED used to mean "cancelled after payment, refund owed". No refund was
  ever recorded, so those orders become REFUND_PENDING.
* Phones are normalized to +8801XXXXXXXXX.
* Offline payment events become kind=PAYMENT.
"""

import re

from django.db import migrations

_BD_MOBILE = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")


def _normalize(value):
    if not value:
        return ""
    compact = re.sub(r"[\s\-().]", "", str(value).strip())
    match = _BD_MOBILE.match(compact)
    return f"+88{match.group(1)}" if match else compact


def forwards(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    OrderStatusEvent = apps.get_model("orders", "OrderStatusEvent")

    Order.objects.filter(
        status__in=["CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED"],
        payments__method="BKASH",
        payments__status="COMPLETED",
    ).update(stock_reserved=True)

    Order.objects.filter(payment_status="REFUNDED", refunded_amount=0).update(
        payment_status="REFUND_PENDING"
    )

    for order in Order.objects.exclude(customer_phone="").only("pk", "customer_phone"):
        normalized = _normalize(order.customer_phone)
        if normalized != order.customer_phone:
            Order.objects.filter(pk=order.pk).update(customer_phone=normalized)

    OrderStatusEvent.objects.filter(note__startswith="Offline payment recorded").update(
        kind="PAYMENT"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0002_order_shipping_refunds_cod"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
