from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Product, ProductVariant
from orders.models import Order, Payment
from orders.services import payments as payment_services
from orders.services.orders import create_order
from orders.utils.choices import OrderStatus, PaymentPlan

User = get_user_model()


def build_order(payment_plan=PaymentPlan.FULL):
    counter = Order.objects.count()
    product = Product.objects.create(
        name=f"Denim Jacket {counter}",
        base_price=Decimal("4200.00"),
        status="ACTIVE",
    )
    variant = ProductVariant.objects.create(
        product=product, size="M", color_name="Blue", sku=f"DJ-{counter}-BLU", stock=4
    )
    return create_order(
        customer_data={
            "customer_name": "Tanvir Hasan",
            "customer_email": "tanvir@example.com",
            "customer_phone": "+8801611223344",
            "address_line": "Flat 4B, Uttara Sector 7",
            "city": "Dhaka",
            "postal_code": "1230",
            "notes": "",
        },
        items_data=[{"variant_id": variant.pk, "quantity": 1}],
        payment_plan=payment_plan,
    )


class BkashPaymentFlowTests(APITestCase):
    def setUp(self):
        self.order = build_order()

    def test_initiate_creates_payment_and_returns_url(self):
        with patch(
            "orders.services.bkash.create_payment",
            return_value={"paymentID": "PM-123", "bkashURL": "https://bkash.example/pay"},
        ) as mock_create:
            response = self.client.post(
                reverse("client-order-pay", args=[self.order.order_number]),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["data"]["payment_url"], "https://bkash.example/pay")
        self.assertEqual(response.data["data"]["bkash_payment_id"], "PM-123")

        amount = mock_create.call_args.kwargs["amount"]
        self.assertEqual(Decimal(amount), Decimal("4270.00"))  # 4200 + 70 Dhaka fee

        payment = Payment.objects.get(bkash_payment_id="PM-123")
        self.assertEqual(payment.status, "INITIATED")
        self.assertEqual(payment.amount, Decimal("4270.00"))

    def test_half_plan_charges_advance(self):
        order = build_order(payment_plan=PaymentPlan.HALF)
        with patch(
            "orders.services.bkash.create_payment",
            return_value={"paymentID": "PM-HALF", "bkashURL": "https://bkash.example/pay"},
        ) as mock_create:
            response = self.client.post(
                reverse("client-order-pay", args=[order.order_number]),
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        expected = (order.total / Decimal("2")).quantize(Decimal("0.01"))
        self.assertEqual(Decimal(mock_create.call_args.kwargs["amount"]), expected)

    def test_execute_settles_payment_and_confirms_order(self):
        with patch(
            "orders.services.bkash.create_payment",
            return_value={"paymentID": "PM-OK", "bkashURL": "https://bkash.example/pay"},
        ):
            self.client.post(
                reverse("client-order-pay", args=[self.order.order_number]),
                format="json",
            )

        with patch(
            "orders.services.bkash.execute_payment",
            return_value={"transactionStatus": "Completed", "trxID": "TRX-999"},
        ):
            payment = payment_services.execute_bkash_payment("PM-OK")

        self.assertEqual(payment.status, "COMPLETED")
        self.assertEqual(payment.bkash_trx_id, "TRX-999")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertEqual(self.order.payment_status, "PAID")
        variant = self.order.items.first().variant
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 3)

    def test_callback_redirects_on_success(self):
        with patch(
            "orders.services.bkash.create_payment",
            return_value={"paymentID": "PM-CB", "bkashURL": "https://bkash.example/pay"},
        ):
            self.client.post(
                reverse("client-order-pay", args=[self.order.order_number]),
                format="json",
            )

        with patch(
            "orders.services.bkash.execute_payment",
            return_value={"transactionStatus": "Completed", "trxID": "TRX-777"},
        ):
            response = self.client.get(
                reverse("bkash-callback"),
                {"paymentID": "PM-CB", "status": "success"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("payment=success", response.url)
        self.assertIn(self.order.order_number, response.url)

    def test_callback_marks_cancelled(self):
        with patch(
            "orders.services.bkash.create_payment",
            return_value={"paymentID": "PM-CX", "bkashURL": "https://bkash.example/pay"},
        ):
            self.client.post(
                reverse("client-order-pay", args=[self.order.order_number]),
                format="json",
            )

        response = self.client.get(
            reverse("bkash-callback"),
            {"paymentID": "PM-CX", "status": "cancel"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("payment=cancelled", response.url)
        payment = Payment.objects.get(bkash_payment_id="PM-CX")
        self.assertEqual(payment.status, "CANCELLED")

    def test_cannot_pay_cancelled_order(self):
        self.order.status = OrderStatus.CANCELLED
        self.order.save(update_fields=["status"])
        response = self.client.post(
            reverse("client-order-pay", args=[self.order.order_number]),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
