from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import Collection, Product, ProductVariant

User = get_user_model()


def make_product(name="Linen Shirt", price="2500.00", **kwargs):
    product = Product.objects.create(
        name=name, base_price=Decimal(price), status="ACTIVE", **kwargs
    )
    ProductVariant.objects.create(
        product=product, size="M", color_name="White", sku=f"SKU-{product.pk}-M-WHT"
    )
    ProductVariant.objects.create(
        product=product, size="L", color_name="White", sku=f"SKU-{product.pk}-L-WHT"
    )
    return product


class CatalogClientAPITests(APITestCase):
    def setUp(self):
        self.collection = Collection.objects.create(name="Summer Edit")
        self.product = make_product()
        self.product.collections.add(self.collection)
        self.draft = Product.objects.create(
            name="Hidden Tee", base_price=Decimal("900.00"), status="DRAFT"
        )

    def test_product_list_shows_only_active(self):
        response = self.client.get(reverse("client-product-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data["data"]]
        self.assertIn("Linen Shirt", names)
        self.assertNotIn("Hidden Tee", names)

    def test_product_list_filters_by_collection(self):
        response = self.client.get(
            reverse("client-product-list"), {"collection": "summer-edit"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_product_detail_by_slug_includes_variants(self):
        response = self.client.get(
            reverse("client-product-detail", args=[self.product.slug])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["variants"]), 2)
        self.assertEqual(response.data["data"]["effective_price"], "2500.00")

    def test_product_detail_404_for_unknown_slug(self):
        response = self.client.get(
            reverse("client-product-detail", args=["does-not-exist"])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_collection_list_and_detail(self):
        response = self.client.get(reverse("client-collection-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"][0]["name"], "Summer Edit")

        response = self.client.get(
            reverse("client-collection-detail", args=["summer-edit"])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["products"]), 1)


class CatalogAdminAPITests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="merch-admin@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)

    def test_admin_can_create_product_with_variants(self):
        payload = {
            "name": "Silk Saree",
            "base_price": "8500.00",
            "sale_price": "7200.00",
            "gender": "WOMEN",
            "status": "ACTIVE",
            "variants": [
                {
                    "size": "ONE_SIZE",
                    "color_name": "Maroon",
                    "sku": "SAREE-MRN",
                    "stock": 5,
                }
            ],
        }
        response = self.client.post(reverse("admin-product-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        product = Product.objects.get(slug="silk-saree")
        self.assertEqual(product.variants.count(), 1)
        self.assertEqual(product.effective_price, Decimal("7200.00"))
        self.assertEqual(product.discount_percent, 15)

    def test_non_staff_cannot_create_product(self):
        User.objects.create_user(email="guest@example.com", password="StrongPass123!")
        self.client.force_authenticate(User.objects.get(email="guest@example.com"))
        response = self.client.post(
            reverse("admin-product-list"),
            {"name": "Hack", "base_price": "1.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_assign_products_to_collection(self):
        collection = Collection.objects.create(name="Eid Drop")
        product = make_product("Kurta Set")

        response = self.client.post(
            reverse("admin-collection-products", args=[collection.pk]),
            {"action": "add", "product_ids": [str(product.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn(product, collection.products.all())

        # Deleting the collection must not delete the product.
        collection.delete()
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_admin_duplicate_variant_combination_rejected(self):
        payload = {
            "name": "Dup Kurta",
            "base_price": "2000.00",
            "variants": [
                {"size": "M", "color_name": "Navy", "sku": "DUP-1", "stock": 1},
                {"size": "M", "color_name": "Navy", "sku": "DUP-2", "stock": 1},
            ],
        }
        response = self.client.post(reverse("admin-product-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
