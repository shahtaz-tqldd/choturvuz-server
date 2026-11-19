from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Collection, Product, ProductVariant
from catalog.utils.choices import ProductKind
from orders.models import Order, OrderAttachment
from orders.services.custom_orders import quote_custom_order
from orders.services.maintenance import cancel_stale_pending_orders
from orders.services.orders import advance_order_status
from orders.utils.choices import (
    AttachmentSource,
    OrderPaymentStatus,
    OrderStatus,
    OrderType,
)

User = get_user_model()

CLOUD = "https://res.cloudinary.com/demo"
UPLOAD_URL = f"{CLOUD}/image/upload/v1712/choturvuz/custom-orders/uploads/2026-11/logo.png"
UPLOAD_URL_2 = f"{CLOUD}/image/upload/v1712/choturvuz/custom-orders/uploads/2026-11/back.png"

STORAGE_SETTINGS = dict(
    CLOUDINARY_CLOUD_NAME="demo",
    CLOUDINARY_ROOT_FOLDER="choturvuz",
    CLOUDINARY_CUSTOM_ORDERS_FOLDER="choturvuz/custom-orders",
)


def make_template(**overrides):
    data = dict(
        name="Department Jersey",
        kind=ProductKind.CUSTOM,
        status="ACTIVE",
        base_price=Decimal("450.00"),
        design_code="JR-024",
        min_order_quantity=10,
        custom_sizes=["M", "L", "XL"],
        custom_colors=[{"name": "Navy", "hex": "#1f2a44"}, {"name": "White", "hex": "#ffffff"}],
    )
    data.update(overrides)
    return Product.objects.create(**data)


def custom_payload(template, **overrides):
    payload = {
        "customer_name": "Rafi Ahmed",
        "customer_email": "rafi@example.com",
        "customer_phone": "+8801711000000",
        "address_line": "CSE Building, University of Dhaka",
        "city": "Dhaka",
        "payment_plan": "HALF",
        "template_id": str(template.pk),
        "custom_instructions": "Logo on the chest, batch name on the back.",
        "size_guidelines": "Slim fit, M = 38in chest.",
        "items": [
            {"size": "M", "color_name": "navy", "quantity": 6, "note": "Names: A, B, C"},
            {"size": "L", "color_name": "Navy", "quantity": 4},
        ],
        "attachments": [{"url": UPLOAD_URL, "kind": "LOGO", "file_name": "logo.png"}],
    }
    payload.update(overrides)
    return payload


@override_settings(**STORAGE_SETTINGS)
class CustomTemplateCatalogTests(APITestCase):
    def setUp(self):
        self.collection = Collection.objects.create(name="Jerseys")
        self.template = make_template()
        self.template.collections.add(self.collection)
        self.regular = Product.objects.create(
            name="Plain Tee", status="ACTIVE", base_price=Decimal("350.00")
        )
        self.regular.collections.add(self.collection)

    def test_product_list_is_regular_only_by_default(self):
        response = self.client.get(reverse("client-product-list"))
        slugs = [item["slug"] for item in response.data["data"]]
        self.assertEqual(slugs, [self.regular.slug])

        response = self.client.get(reverse("client-product-list"), {"kind": "ALL"})
        self.assertEqual(len(response.data["data"]), 2)

    def test_custom_template_list_and_detail(self):
        response = self.client.get(
            reverse("client-custom-template-list"), {"collection": "jerseys"}
        )
        self.assertEqual(response.status_code, 200)
        [card] = response.data["data"]
        self.assertEqual(card["kind"], "CUSTOM")
        self.assertEqual(card["design_code"], "JR-024")
        self.assertTrue(card["in_stock"])

        response = self.client.get(
            reverse("client-custom-template-detail", args=["jr-024"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["custom_sizes"], ["M", "L", "XL"])
        self.assertEqual(response.data["data"]["min_order_quantity"], 10)

        response = self.client.get(
            reverse("client-custom-template-detail", args=[self.regular.slug])
        )
        self.assertEqual(response.status_code, 404)

    def test_collection_detail_splits_products_and_templates(self):
        response = self.client.get(reverse("client-collection-detail", args=["jerseys"]))
        data = response.data["data"]
        self.assertEqual(data["product_count"], 1)
        self.assertEqual(data["custom_template_count"], 1)
        self.assertEqual([p["slug"] for p in data["products"]], [self.regular.slug])
        self.assertEqual([p["slug"] for p in data["custom_templates"]], [self.template.slug])


class AdminCustomTemplateTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="merch@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse("admin-product-list")

    def payload(self, **overrides):
        data = {
            "name": "Tour Hoodie",
            "kind": "CUSTOM",
            "status": "ACTIVE",
            "base_price": "900.00",
            "design_code": "hd-07",
            "min_order_quantity": 15,
            "custom_sizes": ["XL", "M", "M"],
            "custom_colors": [{"name": "Black", "hex": "#000000"}],
            "production_days": 7,
        }
        data.update(overrides)
        return data

    def test_admin_creates_template_with_normalised_options(self):
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["kind"], "CUSTOM")
        self.assertEqual(data["design_code"], "HD-07")
        self.assertEqual(data["custom_sizes"], ["M", "XL"])

        response = self.client.get(self.url, {"kind": "CUSTOM"})
        self.assertEqual(response.data["meta"]["count"], 1)

    def test_design_code_is_unique_and_templates_have_no_variants(self):
        self.client.post(self.url, self.payload(), format="json")
        response = self.client.post(
            self.url, self.payload(name="Another", design_code="HD-07"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("design_code", response.data["errors"])

        response = self.client.post(
            self.url,
            self.payload(
                name="With variants",
                design_code="HD-08",
                variants=[{"size": "M", "sku": "X-1", "stock": 3}],
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("variants", response.data["errors"])

    def test_regular_product_with_variants_cannot_silently_become_template(self):
        product = Product.objects.create(name="Tee", status="ACTIVE", base_price=Decimal("300"))
        ProductVariant.objects.create(product=product, size="M", sku="TEE-M", stock=2)
        url = reverse("admin-product-detail", args=[product.pk])

        response = self.client.patch(url, {"kind": "CUSTOM"}, format="json")
        self.assertEqual(response.status_code, 400)

        response = self.client.patch(url, {"kind": "CUSTOM", "variants": []}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["kind"], "CUSTOM")


@override_settings(**STORAGE_SETTINGS)
class CustomOrderFlowTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.template = make_template()
        self.url = reverse("client-custom-order-create")
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )

    def place(self, **overrides):
        return self.client.post(self.url, custom_payload(self.template, **overrides), format="json")

    def test_customer_places_custom_order_for_review(self):
        response = self.place()
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["order_type"], "CUSTOM")
        self.assertEqual(data["status"], "PENDING_REVIEW")
        self.assertTrue(data["is_awaiting_quote"])
        self.assertFalse(data["requires_online_payment"])
        self.assertEqual(data["subtotal"], "4500.00")  # 10 x 450
        self.assertEqual(data["total"], "4570.00")
        self.assertEqual(data["items"][0]["color_name"], "Navy")
        self.assertEqual(data["items"][0]["color_hex"], "#1f2a44")
        self.assertEqual(data["items"][0]["variant_name"], "Navy / M")
        self.assertEqual(data["items"][0]["sku"], "JR-024")
        self.assertEqual(data["items"][0]["note"], "Names: A, B, C")
        self.assertEqual(data["attachments"][0]["kind"], "LOGO")
        self.assertEqual(data["attachments"][0]["source"], "CUSTOMER")

        order = Order.objects.get(order_number=data["order_number"])
        self.assertFalse(order.stock_reserved)
        self.assertEqual(
            order.attachments.get().public_id,
            "choturvuz/custom-orders/uploads/2026-11/logo",
        )

    def test_validation_against_template_options(self):
        too_few = self.place(items=[{"size": "M", "color_name": "Navy", "quantity": 3}])
        self.assertEqual(too_few.status_code, 400)
        self.assertIn("at least 10", too_few.data["message"])

        bad_size = self.place(items=[{"size": "XS", "color_name": "Navy", "quantity": 10}])
        self.assertEqual(bad_size.status_code, 400)
        self.assertIn("not offered", bad_size.data["message"])

        bad_color = self.place(items=[{"size": "M", "color_name": "Pink", "quantity": 10}])
        self.assertEqual(bad_color.status_code, 400)

    def test_attachments_must_come_from_our_upload_folder(self):
        for url in (
            "https://example.com/logo.png",
            f"{CLOUD}/image/upload/v1/choturvuz/images/products/x.png",
        ):
            response = self.place(attachments=[{"url": url}])
            self.assertEqual(response.status_code, 400, url)
            self.assertIn("attachments", response.data["errors"])

    def test_regular_and_draft_products_are_not_templates(self):
        regular = Product.objects.create(name="Tee", status="ACTIVE", base_price=Decimal("1"))
        response = self.place(template_id=str(regular.pk))
        self.assertEqual(response.status_code, 400)
        self.template.status = "DRAFT"
        self.template.save()
        self.assertEqual(self.place().status_code, 400)

    def test_estimate(self):
        response = self.client.post(
            reverse("client-custom-order-estimate"),
            {
                "template_id": str(self.template.pk),
                "city": "Chattogram",
                "payment_plan": "HALF",
                "items": [{"size": "XL", "color_name": "White", "quantity": 12}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        self.assertEqual(data["subtotal"], "5400.00")
        self.assertEqual(data["delivery_fee"], "130.00")
        self.assertEqual(data["advance_amount"], "2765.00")
        self.assertTrue(data["is_estimate"])

    def test_cannot_pay_or_advance_before_quote(self):
        order = Order.objects.get(order_number=self.place().data["data"]["order_number"])
        response = self.client.post(reverse("client-order-pay", args=[order.order_number]))
        self.assertEqual(response.status_code, 400)

        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            reverse("admin-order-detail", args=[order.pk]), {"status": "CONFIRMED"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("quote", response.data["message"])

        response = self.client.patch(
            reverse("admin-order-detail", args=[order.pk]),
            {"status": "CANCELLED", "note": "Artwork not printable"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_quote_opens_payment_and_joins_regular_flow(self):
        order = Order.objects.get(order_number=self.place().data["data"]["order_number"])
        first_line = order.items.order_by("position").first()
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            reverse("admin-custom-order-quote", args=[order.pk]),
            {
                "items": [{"id": str(first_line.pk), "unit_price": "500.00"}],
                "customization_fee": "300.00",
                "discount": "100.00",
                "note": "Includes chest embroidery.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        # 6 x 500 + 4 x 450 = 4800; + 300 fee - 100 discount + 70 delivery
        self.assertEqual(data["subtotal"], "4800.00")
        self.assertEqual(data["total"], "5070.00")
        self.assertEqual(data["status"], "PENDING_PAYMENT")
        self.assertEqual(data["advance_amount"], "2535.00")
        self.assertTrue(data["requires_online_payment"])
        self.assertIsNotNone(data["quoted_at"])

        # The customer sees the quote on the tracking page.
        self.client.force_authenticate(None)
        response = self.client.get(
            reverse("client-order-track"),
            {"order_number": order.order_number, "phone": "01711000000"},
        )
        self.assertEqual(response.status_code, 200)
        notes = [event["note"] for event in response.data["data"]["events"]]
        self.assertTrue(any("5070.00" in note for note in notes))

        # Paid orders cannot be re-quoted.
        order.refresh_from_db()
        order.paid_amount = Decimal("2535.00")
        order.save()
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("admin-custom-order-quote", args=[order.pk]),
            {"customization_fee": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cod_quote_waits_for_confirmation(self):
        order = Order.objects.get(
            order_number=self.place(payment_plan="COD").data["data"]["order_number"]
        )
        quote_custom_order(order)
        self.assertEqual(order.status, OrderStatus.PENDING_CONFIRMATION)
        self.assertEqual(order.payment_status, OrderPaymentStatus.UNPAID)

        # Confirming and shipping follow the regular flow without touching stock.
        advance_order_status(order, OrderStatus.SHIPPED, created_by=self.admin)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SHIPPED)
        self.assertFalse(order.stock_reserved)

    def test_regular_orders_reject_quotes(self):
        order = Order.objects.create(
            order_number="ARS-1", customer_name="X", customer_phone="1",
            address_line="a", city="Dhaka", total=Decimal("10"),
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("admin-custom-order-quote", args=[order.pk]), {}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(CUSTOM_ORDER_QUOTE_EXPIRY_HOURS=72, PENDING_PAYMENT_EXPIRY_HOURS=24)
    def test_expiry_counts_from_quote_not_from_placement(self):
        order = Order.objects.get(order_number=self.place().data["data"]["order_number"])
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        order.refresh_from_db()
        quote_custom_order(order)

        self.assertEqual(cancel_stale_pending_orders(), 0)
        self.assertEqual(
            cancel_stale_pending_orders(now=timezone.now() + timedelta(hours=73)), 1
        )
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_review_orders_never_expire(self):
        self.place()
        self.assertEqual(
            cancel_stale_pending_orders(now=timezone.now() + timedelta(days=30)), 0
        )

    def test_customer_adds_files_with_contact_proof(self):
        order_number = self.place().data["data"]["order_number"]
        url = reverse("client-custom-order-attachments", args=[order_number])
        body = {"attachments": [{"url": UPLOAD_URL_2, "kind": "DESIGN"}]}

        response = self.client.post(url, {**body, "email": "wrong@example.com"}, format="json")
        self.assertEqual(response.status_code, 404)

        response = self.client.post(url, {**body, "email": "RAFI@example.com"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["data"]["order"]["attachments"]), 2)

        # Re-sending the same file does not duplicate it.
        response = self.client.post(url, {**body, "email": "rafi@example.com"}, format="json")
        self.assertEqual(response.data["data"]["added"], [])

    @patch("orders.api.v1.client.views.upload_file")
    def test_public_upload_endpoint(self, upload_file):
        upload_file.return_value = {
            "url": UPLOAD_URL,
            "public_id": "choturvuz/custom-orders/uploads/2026-11/logo",
            "resource_type": "image",
            "format": "png",
            "content_type": "image/png",
        }
        response = self.client.post(
            reverse("client-custom-order-upload"),
            {"file": SimpleUploadedFile("logo.png", b"img", content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["url"], UPLOAD_URL)
        folder = upload_file.call_args.kwargs["folder"]
        self.assertTrue(folder.startswith("choturvuz/custom-orders/uploads/"))

        response = self.client.post(
            reverse("client-custom-order-upload"),
            {"file": SimpleUploadedFile("run.exe", b"MZ")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    @patch("orders.signals.schedule_delete_image")
    def test_admin_attaches_mockup_and_deletes_files(self, schedule_delete):
        order = Order.objects.get(order_number=self.place().data["data"]["order_number"])
        self.client.force_authenticate(self.admin)
        url = reverse("admin-order-attachments", args=[order.pk])

        response = self.client.post(
            url,
            {"url": f"{CLOUD}/image/upload/v1/choturvuz/files/mockup.png", "note": "Front view"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["kind"], "MOCKUP")
        self.assertEqual(response.data["data"]["source"], AttachmentSource.STAFF)

        detail = self.client.get(reverse("admin-order-detail", args=[order.pk])).data["data"]
        self.assertEqual(len(detail["attachments"]), 2)
        self.assertIn("public_id", detail["attachments"][0])

        attachment = OrderAttachment.objects.get(source=AttachmentSource.CUSTOMER)
        response = self.client.delete(
            reverse("admin-order-attachment-detail", args=[order.pk, attachment.pk])
        )
        self.assertEqual(response.status_code, 200)
        schedule_delete.assert_called_once_with(
            public_id=attachment.public_id, resource_type="image"
        )

    def test_admin_list_filters_by_type_and_stats_count_custom(self):
        self.place()
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("admin-order-list"), {"order_type": "CUSTOM"})
        self.assertEqual(response.data["meta"]["count"], 1)
        row = response.data["data"][0]
        self.assertEqual(row["order_type"], OrderType.CUSTOM)
        self.assertEqual(row["total_quantity"], 10)

        response = self.client.get(reverse("admin-order-list"), {"search": "JR-024"})
        self.assertEqual(response.data["meta"]["count"], 1)

        stats = self.client.get(reverse("admin-stats")).data["data"]["orders"]
        self.assertEqual(stats["by_order_type"]["CUSTOM"], 1)
        self.assertEqual(stats["custom_awaiting_quote"], 1)
        self.assertEqual(stats["by_status"]["PENDING_REVIEW"], 1)
