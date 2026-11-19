from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Product, ProductVariant
from orders.models import Order, OrderStatusEvent
from orders.services.maintenance import cancel_stale_pending_orders
from orders.services.orders import (
    create_order,
    transition_order_status,
)
from orders.utils.choices import OrderStatus, PaymentPlan

User = get_user_model()


def make_variant(name="Cotton Panjabi", price="1800.00", stock=5, size="L"):
    product = Product.objects.create(
        name=name, base_price=Decimal(price), status="ACTIVE"
    )
    return ProductVariant.objects.create(
        product=product, size=size, color_name="Ivory", sku=f"SKU-{product.pk}", stock=stock
    )


def customer_payload(**overrides):
    payload = {
        "customer_name": "Nusrat Jahan",
        "customer_email": "nusrat@example.com",
        "customer_phone": "+8801711223344",
        "address_line": "House 12, Road 5, Dhanmondi",
        "city": "Dhaka",
        "payment_plan": "FULL",
    }
    payload.update(overrides)
    return payload


class OrderCreateAPITests(APITestCase):
    def setUp(self):
        cache.clear()  # reset the per-phone order throttle
        self.variant = make_variant()
        self.url = reverse("client-order-create")

    def order_payload(self, **overrides):
        payload = customer_payload()
        payload["items"] = [
            {"variant_id": str(self.variant.pk), "quantity": 2}
        ]
        payload.update(overrides)
        return payload

    def test_guest_can_order_without_auth(self):
        response = self.client.post(
            self.url, self.order_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        order = Order.objects.get(order_number=response.data["data"]["order_number"])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.subtotal, Decimal("3600.00"))
        # Dhaka delivery fee from StoreConfig default (70).
        self.assertEqual(order.delivery_fee, Decimal("70.00"))
        self.assertEqual(order.total, Decimal("3670.00"))
        self.assertEqual(order.status, OrderStatus.PENDING_PAYMENT)
        self.assertEqual(order.events.count(), 1)

    def test_phone_only_order_is_allowed(self):
        response = self.client.post(
            self.url,
            self.order_payload(customer_email=""),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_email_or_phone_is_required(self):
        response = self.client.post(
            self.url,
            self.order_payload(customer_email="", customer_phone=""),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_out_of_stock_rejected(self):
        response = self.client.post(
            self.url, self.order_payload(items=[{"variant_id": str(self.variant.pk), "quantity": 6}]), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Order.objects.count(), 0)

    def test_half_payment_advance_is_half_total(self):
        response = self.client.post(
            self.url, self.order_payload(payment_plan="HALF"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(Decimal(data["advance_amount"]), (Decimal(data["total"]) / 2).quantize(Decimal("0.01")))

    def test_prices_are_computed_server_side(self):
        self.client.post(
            self.url,
            self.order_payload(items=[{"variant_id": str(self.variant.pk), "quantity": 1, "unit_price": "1.00"}]),
            format="json",
        )
        order = Order.objects.first()
        self.assertEqual(order.items.first().unit_price, Decimal("1800.00"))


class OrderTrackAPITests(APITestCase):
    def setUp(self):
        self.variant = make_variant()
        self.order = create_order(
            customer_data={
                "customer_name": "Nusrat Jahan",
                "customer_email": "nusrat@example.com",
                "customer_phone": "+8801711223344",
                "address_line": "House 12, Road 5, Dhanmondi",
                "city": "Dhaka",
                "postal_code": "",
                "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 1}],
        )
        self.url = reverse("client-order-track")

    def test_track_with_email(self):
        response = self.client.get(
            self.url,
            {"order_number": self.order.order_number, "email": "nusrat@example.com"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["order_number"], self.order.order_number)
        self.assertIn("events", response.data["data"])

    def test_track_with_phone(self):
        response = self.client.get(
            self.url,
            {"order_number": self.order.order_number, "phone": "+8801711223344"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_track_with_wrong_email_denied(self):
        response = self.client.get(
            self.url,
            {"order_number": self.order.order_number, "email": "someoneelse@example.com"},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_track_requires_identifier(self):
        response = self.client.get(
            self.url, {"order_number": self.order.order_number}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrderLifecycleTests(APITestCase):
    def setUp(self):
        self.variant = make_variant(stock=10)
        self.order = create_order(
            customer_data={
                "customer_name": "Rafi Ahmed",
                "customer_email": "rafi@example.com",
                "customer_phone": "+8801811223344",
                "address_line": "Plot 8, Bashundhara",
                "city": "Chattogram",
                "postal_code": "",
                "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 3}],
        )

    def test_confirmation_reserves_stock(self):
        from orders.services.orders import confirm_order

        confirm_order(self.order)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 7)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_cancel_returns_stock_and_writes_event(self):
        from orders.services.orders import confirm_order, cancel_order

        confirm_order(self.order)
        cancel_order(self.order, reason="Customer changed mind.")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 10)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        statuses = list(self.order.events.values_list("status", flat=True))
        self.assertIn(OrderStatus.CANCELLED, statuses)

    def test_delivery_transition_records_timestamp(self):
        from orders.services.orders import confirm_order

        confirm_order(self.order)
        transition_order_status(self.order, OrderStatus.PROCESSING)
        transition_order_status(self.order, OrderStatus.SHIPPED)
        transition_order_status(self.order, OrderStatus.DELIVERED)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.delivered_at)

    def test_illegal_transition_rejected(self):
        with self.assertRaises(ValueError):
            transition_order_status(self.order, OrderStatus.DELIVERED)

    def test_stale_pending_orders_are_cancelled(self):
        Order.objects.filter(pk=self.order.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=30)
        )
        cancelled = cancel_stale_pending_orders()
        self.assertEqual(cancelled, 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)


class AdminOrderAPITests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.variant = make_variant()
        self.order = create_order(
            customer_data={
                "customer_name": "Sadia Islam",
                "customer_email": "sadia@example.com",
                "customer_phone": "+8801911223344",
                "address_line": "Road 9, Mirpur",
                "city": "Dhaka",
                "postal_code": "",
                "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 1}],
            payment_plan=PaymentPlan.HALF,
        )

    def test_admin_order_list_and_search(self):
        response = self.client.get(
            reverse("admin-order-list"), {"search": "sadia@example.com"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)

    def test_admin_cannot_skip_statuses(self):
        response = self.client.patch(
            reverse("admin-order-detail", args=[self.order.pk]),
            {"status": "DELIVERED"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_confirms_cod_and_records_due(self):
        response = self.client.patch(
            reverse("admin-order-detail", args=[self.order.pk]),
            {"status": "CONFIRMED", "note": "Cash on delivery confirmed by phone."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        response = self.client.post(
            reverse("admin-order-record-payment", args=[self.order.pk]),
            {"amount": str(self.order.total), "note": "Full cash collected."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "PAID")
        self.assertEqual(self.order.paid_amount, self.order.total)

    def test_admin_payment_list_is_searchable(self):
        response = self.client.get(
            reverse("admin-payment-list"), {"status": "INITIATED"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
