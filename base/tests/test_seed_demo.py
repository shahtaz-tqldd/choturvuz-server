from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Collection, Product, ProductVariant


class SeedDemoCommandTests(TestCase):
    def run_seed(self, *args):
        out = StringIO()
        call_command("seed_demo", *args, stdout=out)
        return out.getvalue()

    def test_seeds_demo_catalog_once(self):
        self.run_seed()
        self.assertEqual(Collection.objects.count(), 5)
        self.assertEqual(Product.objects.filter(kind="REGULAR").count(), 8)
        self.assertEqual(Product.objects.filter(kind="CUSTOM").count(), 6)
        self.assertFalse(ProductVariant.objects.filter(product__kind="CUSTOM").exists())

        output = self.run_seed()
        self.assertIn("0 created", output)
        self.assertEqual(Product.objects.count(), 14)
        from reviews.models import Review
        self.assertEqual(Review.objects.filter(status="APPROVED").count(), 4)

    def test_reset_recreates_and_dry_run_saves_nothing(self):
        self.run_seed("--dry-run")
        self.assertEqual(Product.objects.count(), 0)

        self.run_seed()
        Product.objects.filter(design_code="JR-001").update(base_price=1)
        self.run_seed("--reset")
        self.assertEqual(Product.objects.get(design_code="JR-001").base_price, 480)


class SeedDemoOnceTests(TestCase):
    def run_once(self):
        out = StringIO()
        call_command("seed_demo", "--once", stdout=out)
        return out.getvalue()

    def test_seeds_fresh_database_only_once(self):
        from base.models import StoreConfig

        self.run_once()
        self.assertEqual(Product.objects.count(), 14)
        from reviews.models import Review
        self.assertEqual(Review.objects.filter(status="APPROVED").count(), 4)
        self.assertIsNotNone(StoreConfig.objects.get().demo_seeded_at)

        # Admin deletes everything to start from scratch; a restart must not re-seed.
        Product.objects.all().delete()
        Collection.objects.all().delete()
        self.assertIn("already seeded", self.run_once())
        self.assertEqual(Product.objects.count(), 0)

    def test_existing_catalog_is_left_alone(self):
        Collection.objects.create(name="Our Own Collection")
        self.assertIn("already has data", self.run_once())
        self.assertEqual(Product.objects.count(), 0)
        self.assertIn("already seeded", self.run_once())
