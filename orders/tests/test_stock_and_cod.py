from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import StockMovement
from catalog.utils.choices import StockMovementReason
from orders.models import Order, Payment
from orders.services.maintenance import cancel_stale_pending_orders
from orders.services.orders import (
    OutOfStockError,
    cancel_order,
    create_order,
    status_path,
    transition_order_status,
)
from orders.services.payments import settle_bkash_payment
from orders.tests.test_orders import customer_payload, make_variant
from orders.utils.choices import (
    OrderEventKind,
    OrderPaymentStatus,
    OrderStatus,
    PaymentPlan,
)

User = get_user_model()

CUSTOMER = {
    "customer_name": "Rafi Ahmed",
    "customer_email": "rafi@example.com",
    "customer_phone": "01811223344",
    "address_line": "Plot 8, Bashundhara",
    "city": "Dhaka",
    "postal_code": "",
    "notes": "",
}


def place(variant, quantity=1, plan=PaymentPlan.FULL, **customer):
    return create_order(
        customer_data={**CUSTOMER, **customer},
        items_data=[{"variant_id": variant.pk, "quantity": quantity}],
        payment_plan=plan,
    )


class StockReservationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )
        self.variant = make_variant(stock=5)

    def stock(self):
        self.variant.refresh_from_db()
        return self.variant.stock

    def test_stock_is_reserved_when_order_is_placed(self):
        order = place(self.variant, quantity=2)
        self.assertEqual(self.stock(), 3)
        self.assertTrue(order.stock_reserved)
        movement = StockMovement.objects.get(order=order)
        self.assertEqual(movement.delta, -2)
        self.assertEqual(movement.reason, StockMovementReason.ORDER_PLACED)

    def test_last_item_cannot_be_sold_twice(self):
        place(self.variant, quantity=5)
        with self.assertRaises(OutOfStockError):
            place(self.variant, quantity=1)
        self.assertEqual(self.stock(), 0)
        self.assertEqual(Order.objects.count(), 1)

    def test_admin_confirm_then_cancel_leaves_stock_unchanged(self):
        """A1: PENDING_PAYMENT -> CONFIRMED -> CANCELLED through the admin API."""
        self.client.force_authenticate(self.admin)
        order = place(self.variant, quantity=2)
        url = reverse("admin-order-detail", args=[order.pk])

        response = self.client.patch(url, {"status": "CONFIRMED"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.stock(), 3)

        response = self.client.patch(
            url, {"status": "CANCELLED", "note": "Customer changed mind."}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.stock(), 5)

        # Cancelling twice must not add stock again.
        order.refresh_from_db()
        cancel_order(order)
        self.assertEqual(self.stock(), 5)

    def test_legacy_order_without_reservation_reserves_on_confirm(self):
        order = place(self.variant, quantity=2)
        # Simulate an order placed before reservation at checkout existed.
        Order.objects.filter(pk=order.pk).update(stock_reserved=False)
        self.variant.stock = 5
        self.variant.save()

        transition_order_status(order, OrderStatus.CONFIRMED)
        self.assertEqual(self.stock(), 3)
        cancel_order(order)
        self.assertEqual(self.stock(), 5)

    def test_legacy_order_never_reserved_does_not_inflate_stock_on_cancel(self):
        order = place(self.variant, quantity=2)
        Order.objects.filter(pk=order.pk).update(
            stock_reserved=False, status=OrderStatus.CONFIRMED
        )
        self.variant.stock = 5
        self.variant.save()
        order.refresh_from_db()
        cancel_order(order)
        self.assertEqual(self.stock(), 5)

    def test_legacy_confirm_with_missing_stock_is_rejected_cleanly(self):
        self.client.force_authenticate(self.admin)
        order = place(self.variant, quantity=2)
        Order.objects.filter(pk=order.pk).update(stock_reserved=False)
        self.variant.stock = 1
        self.variant.save()

        response = self.client.patch(
            reverse("admin-order-detail", args=[order.pk]),
            {"status": "CONFIRMED"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING_PAYMENT)
        self.assertEqual(self.stock(), 1)

    def test_bkash_payment_for_legacy_order_without_stock_never_crashes(self):
        order = place(self.variant, quantity=2)
        Order.objects.filter(pk=order.pk).update(stock_reserved=False)
        self.variant.stock = 0
        self.variant.save()
        Payment.objects.create(
            order=order, amount=order.total, bkash_payment_id="PAY-LEGACY"
        )

        settle_bkash_payment(bkash_payment_id="PAY-LEGACY", trx_id="TRX1")

        order.refresh_from_db()
        self.assertEqual(order.payment_status, OrderPaymentStatus.PAID)
        self.assertEqual(order.status, OrderStatus.PENDING_PAYMENT)
        self.assertTrue(order.events.filter(kind=OrderEventKind.NOTE).exists())
        self.assertEqual(self.stock(), 0)

    def test_expired_unpaid_order_returns_stock(self):
        order = place(self.variant, quantity=2)
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=30)
        )
        self.assertEqual(cancel_stale_pending_orders(), 1)
        self.assertEqual(self.stock(), 5)

    def test_expiry_skips_orders_with_payment_in_progress(self):
        order = place(self.variant, quantity=1)
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=30)
        )
        Payment.objects.create(order=order, amount=order.total, bkash_payment_id="PAY-NOW")
        self.assertEqual(cancel_stale_pending_orders(), 0)

    def test_payment_after_cancellation_flags_refund(self):
        order = place(self.variant, quantity=1)
        Payment.objects.create(order=order, amount=order.total, bkash_payment_id="PAY-LATE")
        cancel_order(order, reason="Expired")
        settle_bkash_payment(bkash_payment_id="PAY-LATE", trx_id="TRX2")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(order.payment_status, OrderPaymentStatus.REFUND_PENDING)


class CashOnDeliveryTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.variant = make_variant(stock=5)
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )

    def post_order(self, **overrides):
        payload = customer_payload(payment_plan="COD", **overrides)
        payload["items"] = [{"variant_id": str(self.variant.pk), "quantity": 1}]
        return self.client.post(reverse("client-order-create"), payload, format="json")

    def test_cod_order_waits_for_confirmation(self):
        response = self.post_order()
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["payment_plan"], "COD")
        self.assertEqual(data["status"], OrderStatus.PENDING_CONFIRMATION)
        self.assertTrue(data["is_cod"])
        self.assertFalse(data["requires_online_payment"])
        self.assertEqual(data["advance_amount"], "0.00")

    def test_cod_order_cannot_start_bkash(self):
        order_number = self.post_order().data["data"]["order_number"]
        response = self.client.post(reverse("client-order-pay", args=[order_number]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["success"])

    def test_cod_orders_are_not_auto_expired(self):
        order_number = self.post_order().data["data"]["order_number"]
        Order.objects.filter(order_number=order_number).update(
            created_at=timezone.now() - timezone.timedelta(days=3)
        )
        self.assertEqual(cancel_stale_pending_orders(), 0)

    def test_cod_can_be_disabled(self):
        from base.models import StoreConfig

        StoreConfig.objects.create(allow_cod=False)
        response = self.post_order()
        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_plan", response.data["errors"])

    def test_admin_can_filter_and_confirm_cod(self):
        order_number = self.post_order().data["data"]["order_number"]
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("admin-order-list"), {"payment_plan": "COD"})
        self.assertEqual(response.data["meta"]["count"], 1)

        order = Order.objects.get(order_number=order_number)
        response = self.client.patch(
            reverse("admin-order-detail", args=[order.pk]),
            {"status": "CONFIRMED", "note": "Confirmed by phone."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["status"], OrderStatus.CONFIRMED)

    def test_phone_is_normalized_and_tracking_accepts_local_format(self):
        response = self.post_order(customer_phone="01711223344")
        data = response.data["data"]
        self.assertEqual(data["customer_phone"], "+8801711223344")
        response = self.client.get(
            reverse("client-order-track"),
            {"order_number": data["order_number"], "phone": "01711-223344"},
        )
        self.assertEqual(response.status_code, 200)

    def test_orders_are_rate_limited_per_phone(self):
        for _ in range(5):
            self.variant.stock = 5
            self.variant.save()
            self.assertEqual(self.post_order().status_code, 201)
        response = self.post_order()
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.data["success"])


class QuoteTests(APITestCase):
    def setUp(self):
        self.variant = make_variant(stock=2, price="1000.00")
        self.sold_out = make_variant(name="Silk Saree", stock=0, size="M")
        self.url = reverse("client-order-quote")

    def test_quote_prices_cart_and_flags_unavailable_lines(self):
        response = self.client.post(
            self.url,
            {
                "city": "Dhaka",
                "payment_plan": "HALF",
                "items": [
                    {"variant_id": str(self.variant.pk), "quantity": 2},
                    {"variant_id": str(self.sold_out.pk), "quantity": 1},
                    {"variant_id": "00000000-0000-0000-0000-000000000000", "quantity": 1},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        self.assertEqual(data["subtotal"], "2000.00")
        self.assertEqual(data["delivery_fee"], "70.00")
        self.assertEqual(data["total"], "2070.00")
        self.assertEqual(data["advance_amount"], "1035.00")
        self.assertFalse(data["all_available"])
        reasons = [line["reason"] for line in data["items"]]
        self.assertEqual(reasons, [None, "sold_out", "unavailable"])

    def test_free_delivery_threshold(self):
        from base.models import StoreConfig

        StoreConfig.objects.create(free_delivery_threshold=Decimal("1500.00"))
        response = self.client.post(
            self.url,
            {"city": "Sylhet", "items": [{"variant_id": str(self.variant.pk), "quantity": 2}]},
            format="json",
        )
        self.assertEqual(response.data["data"]["delivery_fee"], "0.00")


class AdminOrderOperationsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True, name="Ops"
        )
        self.client.force_authenticate(self.admin)
        self.variant = make_variant(stock=10)
        self.order = place(self.variant, quantity=1, plan=PaymentPlan.COD)

    def test_bulk_status_steps_through_intermediate_states(self):
        other = place(self.variant, quantity=1, plan=PaymentPlan.COD)
        cancelled = place(self.variant, quantity=1, plan=PaymentPlan.COD)
        cancel_order(cancelled)

        response = self.client.post(
            reverse("admin-order-bulk-status"),
            {
                "ids": [str(self.order.pk), str(other.pk), str(cancelled.pk)],
                "status": "SHIPPED",
                "courier": "Pathao",
                "tracking_number": "PTH-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data["data"]["results"]
        self.assertEqual([r["ok"] for r in results], [True, True, False])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.SHIPPED)
        self.assertEqual(self.order.courier, "Pathao")
        self.assertIsNotNone(self.order.shipped_at)
        self.assertEqual(
            list(self.order.events.values_list("status", flat=True)),
            ["PENDING_CONFIRMATION", "CONFIRMED", "PROCESSING", "SHIPPED"],
        )

    def test_status_path_never_routes_through_cancelled(self):
        self.assertEqual(
            status_path(OrderStatus.PENDING_PAYMENT, OrderStatus.DELIVERED),
            ["CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED"],
        )
        self.assertIsNone(status_path(OrderStatus.CANCELLED, OrderStatus.SHIPPED))

    def test_lookup_by_multiple_order_numbers(self):
        other = place(self.variant, quantity=1)
        place(self.variant, quantity=1)
        response = self.client.get(
            reverse("admin-order-list"),
            {"order_number": f"{self.order.order_number},{other.order_number.lower()}"},
        )
        self.assertEqual(response.data["meta"]["count"], 2)

    def test_update_customer_recalculates_delivery_fee(self):
        response = self.client.patch(
            reverse("admin-order-customer-update", args=[self.order.pk]),
            {"city": "Khulna", "customer_phone": "01911000111"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        self.assertEqual(data["delivery_fee"], "130.00")
        self.assertEqual(data["customer_phone"], "+8801911000111")
        edit = [e for e in data["events"] if e["kind"] == "EDIT"][0]
        self.assertEqual(edit["created_by"]["email"], "ops@example.com")

    def test_internal_notes_hidden_from_tracking(self):
        response = self.client.post(
            reverse("admin-order-notes", args=[self.order.pk]),
            {"note": "Customer asked to deliver after 5pm."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(None)
        response = self.client.get(
            reverse("client-order-track"),
            {"order_number": self.order.order_number, "phone": "+8801811223344"},
        )
        self.assertEqual(response.status_code, 200)
        notes = [event["note"] for event in response.data["data"]["events"]]
        self.assertNotIn("Customer asked to deliver after 5pm.", notes)

    def test_refund_flow(self):
        transition_order_status(self.order, OrderStatus.CONFIRMED)
        self.client.post(
            reverse("admin-order-record-payment", args=[self.order.pk]),
            {"amount": "500.00"},
            format="json",
        )
        transition_order_status(self.order, OrderStatus.CANCELLED, note="Returned")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.REFUND_PENDING)

        url = reverse("admin-order-refund", args=[self.order.pk])
        response = self.client.post(
            url, {"amount": "600.00", "method": "BKASH"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            url, {"amount": "500.00", "method": "BKASH", "trx_id": "RF123"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, OrderPaymentStatus.REFUNDED)
        self.assertEqual(self.order.refunded_amount, Decimal("500.00"))

    @patch("orders.services.bkash.refund_payment")
    def test_refund_via_bkash_api(self, refund_payment):
        refund_payment.return_value = {"refundTrxID": "BKREF1", "transactionStatus": "Completed"}
        order = place(self.variant, quantity=1)
        Payment.objects.create(
            order=order, amount=order.total, bkash_payment_id="PAY-R", status="COMPLETED",
            bkash_trx_id="TRX-R", completed_at=timezone.now(),
        )
        Order.objects.filter(pk=order.pk).update(
            paid_amount=order.total, payment_status=OrderPaymentStatus.PAID
        )
        order.refresh_from_db()
        cancel_order(order)
        response = self.client.post(
            reverse("admin-order-refund", args=[order.pk]),
            {"amount": str(order.total), "method": "BKASH", "via_bkash": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["refund"]["trx_id"], "BKREF1")
        refund_payment.assert_called_once()

    def test_stats_and_customers(self):
        response = self.client.get(reverse("admin-stats"))
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        self.assertEqual(data["orders"]["count"], 1)
        self.assertEqual(data["orders"]["by_status"]["PENDING_CONFIRMATION"], 1)
        self.assertEqual(data["revenue"]["gross"], str(self.order.total))
        self.assertEqual(data["revenue"]["outstanding_cod"], str(self.order.total))
        self.assertTrue(data["daily"])

        response = self.client.get(reverse("admin-order-customers"))
        self.assertEqual(response.status_code, 200)
        row = response.data["data"][0]
        self.assertEqual(row["customer_phone"], "+8801811223344")
        self.assertEqual(row["order_count"], 1)

    def test_stats_rejects_bad_dates(self):
        response = self.client.get(reverse("admin-stats"), {"date_from": "nope"})
        self.assertEqual(response.status_code, 400)
