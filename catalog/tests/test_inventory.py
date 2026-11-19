from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from catalog.models import Collection, Product, ProductVariant, StockMovement
from orders.services.orders import create_order

User = get_user_model()


def make_product(name="Linen Shirt", price="2500.00", stocks=(5, 0), **kwargs):
    product = Product.objects.create(
        name=name, base_price=Decimal(price), status="ACTIVE", **kwargs
    )
    for size, stock in zip(("M", "L", "XL"), stocks):
        ProductVariant.objects.create(
            product=product, size=size, color_name="White",
            sku=f"{product.slug}-{size}".upper(), stock=stock,
        )
    return product


class AdminInventoryTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.product = make_product()
        self.variant = self.product.variants.get(size="M")

    def test_set_stock(self):
        response = self.client.patch(
            reverse("admin-variant-detail", args=[self.variant.pk]),
            {"stock": 12, "note": "Recount"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["stock"], 12)
        movement = StockMovement.objects.get(variant=self.variant)
        self.assertEqual((movement.delta, movement.reason), (7, "SET"))

    def test_adjust_stock_cannot_go_negative(self):
        url = reverse("admin-variant-adjust", args=[self.variant.pk])
        response = self.client.post(url, {"delta": -2, "reason": "damaged"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["stock"], 3)
        response = self.client.post(url, {"delta": -4}, format="json")
        self.assertEqual(response.status_code, 409)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)

    def test_bulk_stock_by_sku_is_all_or_nothing(self):
        url = reverse("admin-variant-bulk-stock")
        response = self.client.post(
            url, [{"sku": self.variant.sku, "stock": 9}, {"sku": "NOPE", "stock": 1}], format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["errors"]["unknown_skus"], ["NOPE"])
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)

        response = self.client.post(url, [{"sku": self.variant.sku, "stock": 9}], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["updated"], 1)

    def test_stock_movement_history(self):
        create_order(
            customer_data={
                "customer_name": "A", "customer_email": "", "customer_phone": "01711000000",
                "address_line": "X", "city": "Dhaka", "postal_code": "", "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 1}],
        )
        response = self.client.get(
            reverse("admin-stock-movements"), {"variant": str(self.variant.pk)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"][0]["reason"], "ORDER_PLACED")
        self.assertTrue(response.data["data"][0]["order_number"])

    def test_product_patch_does_not_overwrite_stock_changed_by_orders(self):
        """A5: the admin loads stock 5, an order takes 2, the admin saves 5."""
        url = reverse("admin-product-detail", args=[self.product.pk])
        loaded = self.client.get(url).data["data"]

        create_order(
            customer_data={
                "customer_name": "A", "customer_email": "", "customer_phone": "01711000000",
                "address_line": "X", "city": "Dhaka", "postal_code": "", "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 2}],
        )

        variants = [
            {k: v[k] for k in ("id", "size", "color_name", "sku", "stock", "is_active")}
            for v in loaded["variants"]
        ]
        # Untouched M line (still 5 in the form), edited L line (0 -> 4).
        for v in variants:
            if v["size"] == "L":
                v["stock"] = 4
        response = self.client.patch(
            url,
            {"name": "Linen Shirt v2", "variants": variants,
             "stock_snapshot_at": loaded["stock_snapshot_at"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        stocks = {v["size"]: v["stock"] for v in response.data["data"]["variants"]}
        self.assertEqual(stocks, {"M": 3, "L": 4})

    def test_removing_variant_with_orders_is_a_validation_error(self):
        create_order(
            customer_data={
                "customer_name": "A", "customer_email": "", "customer_phone": "01711000000",
                "address_line": "X", "city": "Dhaka", "postal_code": "", "notes": "",
            },
            items_data=[{"variant_id": self.variant.pk, "quantity": 1}],
        )
        other = self.product.variants.get(size="L")
        response = self.client.patch(
            reverse("admin-product-detail", args=[self.product.pk]),
            {"variants": [{"id": str(other.pk), "size": "L", "color_name": "White", "sku": other.sku}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["success"])
        self.assertTrue(ProductVariant.objects.filter(pk=self.variant.pk).exists())


class AdminProductListAndBulkTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="ops@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.shirt = make_product("Linen Shirt", stocks=(5, 2))
        self.saree = make_product("Silk Saree", price="9000.00", stocks=(0, 0), sale_price=Decimal("7000.00"))
        self.panjabi = make_product("Cotton Panjabi", price="3000.00", stocks=(10, 10), is_featured=True)
        self.url = reverse("admin-product-list")

    def names(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return sorted(item["name"] for item in response.data["data"])

    def test_filters(self):
        self.assertEqual(self.names(is_featured="true"), ["Cotton Panjabi"])
        self.assertEqual(self.names(on_sale="true"), ["Silk Saree"])
        self.assertEqual(self.names(stock="out"), ["Silk Saree"])
        self.assertEqual(self.names(stock="low"), ["Linen Shirt", "Silk Saree"])
        self.assertEqual(self.names(search="silk-saree-m"), ["Silk Saree"])

    def test_ordering(self):
        response = self.client.get(self.url, {"ordering": "effective_price"})
        self.assertEqual(
            [item["name"] for item in response.data["data"]],
            ["Linen Shirt", "Cotton Panjabi", "Silk Saree"],
        )

    def test_bulk_sale_percent_and_feature(self):
        url = reverse("admin-product-bulk")
        response = self.client.post(
            url, {"ids": [str(self.shirt.pk), str(self.panjabi.pk)], "action": "set_sale_percent", "value": 20},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.sale_price, Decimal("2000.00"))

        response = self.client.post(
            url, {"ids": [str(self.shirt.pk)], "action": "set_status", "value": "BOGUS"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_bulk_delete_refuses_products_with_orders(self):
        create_order(
            customer_data={
                "customer_name": "A", "customer_email": "", "customer_phone": "01711000000",
                "address_line": "X", "city": "Dhaka", "postal_code": "", "notes": "",
            },
            items_data=[{"variant_id": self.shirt.variants.first().pk, "quantity": 1}],
        )
        response = self.client.post(
            reverse("admin-product-bulk"),
            {"ids": [str(self.shirt.pk), str(self.panjabi.pk)], "action": "delete"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Product.objects.filter(pk=self.panjabi.pk).exists())


class StorefrontCatalogTests(APITestCase):
    def setUp(self):
        self.shirt = make_product("Linen Shirt", stocks=(5, 0))
        self.saree = make_product("Silk Saree", price="9000.00", stocks=(0,), sale_price=Decimal("2000.00"))
        self.url = reverse("client-product-list")

    def test_cards_include_in_stock(self):
        response = self.client.get(self.url)
        stock = {item["name"]: item["in_stock"] for item in response.data["data"]}
        self.assertEqual(stock, {"Linen Shirt": True, "Silk Saree": False})

    def test_sort_and_filter_by_effective_price(self):
        response = self.client.get(self.url, {"ordering": "effective_price"})
        self.assertEqual([i["name"] for i in response.data["data"]], ["Silk Saree", "Linen Shirt"])
        response = self.client.get(self.url, {"max_price": "2100"})
        self.assertEqual([i["name"] for i in response.data["data"]], ["Silk Saree"])

    def test_size_and_in_stock_filters(self):
        response = self.client.get(self.url, {"size": "L", "in_stock": "true"})
        self.assertEqual(response.data["data"], [])
        response = self.client.get(self.url, {"size": "m", "in_stock": "true"})
        self.assertEqual([i["name"] for i in response.data["data"]], ["Linen Shirt"])

    def test_collections_have_type_and_position(self):
        Collection.objects.create(name="Eid Edit", type="FESTIVE", position=2)
        Collection.objects.create(name="Panjabi", type="CATEGORY", position=1)
        response = self.client.get(reverse("client-collection-list"))
        self.assertEqual([c["name"] for c in response.data["data"]], ["Panjabi", "Eid Edit"])
        response = self.client.get(reverse("client-collection-list"), {"type": "festive"})
        self.assertEqual([c["name"] for c in response.data["data"]], ["Eid Edit"])


@override_settings(
    STOREFRONT_REVALIDATE_URL="http://storefront.test/api/revalidate",
    STOREFRONT_REVALIDATE_SECRET="s3cret",
)
class RevalidationTests(APITestCase):
    @patch("app.tasks.send_storefront_revalidation.delay")
    def test_product_change_sends_tags_once_after_commit(self, delay):
        with self.captureOnCommitCallbacks(execute=True):
            product = make_product("Linen Shirt")
        delay.assert_called_once()
        tags = delay.call_args.args[0]
        self.assertIn("products", tags)
        self.assertIn(f"product:{product.slug}", tags)

    @patch("app.tasks.send_storefront_revalidation.delay")
    def test_store_config_change(self, delay):
        from base.models import StoreConfig

        with self.captureOnCommitCallbacks(execute=True):
            StoreConfig.objects.create()
        self.assertEqual(delay.call_args.args[0], ["store-config"])

    @patch("app.tasks.requests.post")
    def test_task_posts_secret_header(self, post):
        from app.tasks import send_storefront_revalidation

        post.return_value.status_code = 200
        send_storefront_revalidation(["products"])
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"]["x-revalidate-secret"], "s3cret")
        self.assertEqual(kwargs["json"], {"tags": ["products"]})
