"""Load demo collections, products, custom order templates and reviews from JSON.

    python manage.py seed_demo                 # create whatever is missing
    python manage.py seed_demo --reset         # delete the file's items, then recreate them
    python manage.py seed_demo --dry-run       # validate the file, change nothing
    python manage.py seed_demo --file path/to/other.json
    python manage.py seed_demo --once          # app startup: seed only on a fresh database

Items are matched by slug (derived from the name), and templates also by
design code, so running it twice never duplicates anything. Every item goes
through the same serializers as the admin API, so the same rules apply.
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.utils import timezone
from django.utils.text import slugify

from base.models import StoreConfig
from catalog.api.v1.admin.serializers import CollectionWriteSerializer, ProductWriteSerializer
from catalog.models import Collection, Product
from catalog.utils.choices import ProductKind
from reviews.models import Review
from reviews.services import apply_status
from reviews.utils.choices import ReviewSource, ReviewStatus

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "demo" / "demo_products.json"


class _DryRun(Exception):
    pass


def _color_code(name):
    return re.sub(r"[^A-Za-z0-9]", "", name or "").upper()[:4] or "STD"


def expand_variant_matrix(matrix):
    """{"sku_prefix", "sizes", "colors": [{name, hex}], "stock", "price"?} -> variants."""
    prefix = matrix.get("sku_prefix")
    if not prefix:
        raise CommandError("variant_matrix needs a sku_prefix.")
    colors = matrix.get("colors") or [{"name": "", "hex": ""}]
    variants = []
    for color in colors:
        for size in matrix.get("sizes") or []:
            parts = [prefix, _color_code(color.get("name"))] if color.get("name") else [prefix]
            variant = {
                "size": size,
                "color_name": color.get("name", ""),
                "color_hex": color.get("hex", ""),
                "sku": "-".join(parts + [size]),
                "stock": int(matrix.get("stock", 0)),
            }
            if matrix.get("price") is not None:
                variant["price"] = matrix["price"]
            variants.append(variant)
    return variants


class Command(BaseCommand):
    help = "Create demo collections, products and custom templates from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="Path to the demo JSON file.")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the items listed in the file first, then recreate them.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Validate everything and roll back."
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Seed only if demo data was never seeded and the catalog is empty. "
                "Used on app startup."
            ),
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc

        self.counts = {"created": 0, "skipped": 0, "deleted": 0}
        try:
            with transaction.atomic():
                if options["once"] and not self._should_seed_once():
                    return
                if options["reset"]:
                    self._reset(data)
                collection_ids = self._seed_collections(data.get("collections", []))
                defaults = data.get("defaults", {})
                for item in data.get("products", []):
                    self._seed_product(item, defaults, collection_ids, ProductKind.REGULAR)
                for item in data.get("custom_templates", []):
                    self._seed_product(item, defaults, collection_ids, ProductKind.CUSTOM)
                for item in data.get("reviews", []):
                    self._seed_review(item)
                if options["dry_run"]:
                    raise _DryRun
                self._mark_seeded()
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run: the file is valid, nothing was saved."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready: {created} created, {skipped} already existed, "
                "{deleted} deleted.".format(**self.counts)
            )
        )

    # -- once ----------------------------------------------------------------

    def _config(self):
        config = StoreConfig.objects.select_for_update().first()
        return config or StoreConfig.objects.create()

    def _should_seed_once(self):
        config = self._config()
        if config.demo_seeded_at is not None:
            self.stdout.write(
                self.style.WARNING("Demo data was already seeded. Skipping.")
            )
            return False
        if Product.objects.exists() or Collection.objects.exists():
            # An existing store with its own catalog: never mix demo data in.
            self._mark_seeded()
            self.stdout.write(
                self.style.WARNING("Catalog already has data. Skipping demo seed.")
            )
            return False
        return True

    def _mark_seeded(self):
        config = self._config()
        if config.demo_seeded_at is None:
            config.demo_seeded_at = timezone.now()
            config.save(update_fields=["demo_seeded_at"])

    # -- reset --------------------------------------------------------------

    def _reset(self, data):
        items = data.get("products", []) + data.get("custom_templates", [])
        for item in items:
            lookup = Q(slug=slugify(item["name"]))
            if item.get("design_code"):
                lookup |= Q(design_code__iexact=item["design_code"])
            for product in Product.objects.filter(lookup):
                try:
                    with transaction.atomic():
                        product.delete()
                    self.counts["deleted"] += 1
                except ProtectedError:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  kept '{product.name}': it has orders (archive it in the admin instead)"
                        )
                    )
        for item in data.get("reviews", []):
            deleted, _ = self._review_lookup(item).delete()
            self.counts["deleted"] += deleted
        for item in data.get("collections", []):
            slug = item.get("slug") or slugify(item["name"])
            deleted, _ = Collection.objects.filter(slug=slug).delete()
            self.counts["deleted"] += 1 if deleted else 0

    # -- collections ------------------------------------------------------

    def _seed_collections(self, collections):
        ids = {}
        for item in collections:
            slug = item.get("slug") or slugify(item["name"])
            existing = Collection.objects.filter(Q(slug=slug) | Q(name__iexact=item["name"])).first()
            if existing:
                ids[slug] = existing.pk
                self._log("skip", "collection", item["name"])
                continue
            payload = {key: value for key, value in item.items() if key != "slug"}
            serializer = CollectionWriteSerializer(data=payload)
            self._validate(serializer, f"collection '{item['name']}'")
            collection = serializer.save()
            if item.get("slug") and collection.slug != item["slug"]:
                collection.slug = item["slug"]
                collection.save(update_fields=["slug", "updated_at"])
            ids[collection.slug] = collection.pk
            self._log("create", "collection", item["name"])
        return ids

    # -- products / templates ---------------------------------------------

    def _seed_product(self, item, defaults, collection_ids, kind):
        label = "template" if kind == ProductKind.CUSTOM else "product"
        name = item["name"]
        lookup = Q(slug=slugify(name))
        if item.get("design_code"):
            lookup |= Q(design_code__iexact=item["design_code"])
        if Product.objects.filter(lookup).exists():
            self._log("skip", label, name)
            return

        payload = {**defaults, **item, "kind": kind}
        slugs = payload.pop("collections", [])
        unknown = [slug for slug in slugs if slug not in collection_ids]
        unknown_in_db = [
            slug for slug in unknown if not Collection.objects.filter(slug=slug).exists()
        ]
        if unknown_in_db:
            raise CommandError(
                f"{label} '{name}' uses unknown collection(s): {', '.join(unknown_in_db)}"
            )
        payload["collection_ids"] = [
            str(collection_ids.get(slug) or Collection.objects.get(slug=slug).pk)
            for slug in slugs
        ]

        matrix = payload.pop("variant_matrix", None)
        if matrix is not None:
            if kind == ProductKind.CUSTOM:
                raise CommandError(f"template '{name}' cannot have a variant_matrix.")
            payload["variants"] = expand_variant_matrix(matrix)
        for index, image in enumerate(payload.get("images", [])):
            image.setdefault("position", index)

        serializer = ProductWriteSerializer(data=payload)
        self._validate(serializer, f"{label} '{name}'")
        serializer.save()
        self._log("create", label, name)

    # -- reviews ------------------------------------------------------------

    @staticmethod
    def _review_lookup(item):
        return Review.objects.filter(
            customer_name=item["customer_name"], comment=item["comment"]
        )

    def _seed_review(self, item):
        name = item["customer_name"]
        if self._review_lookup(item).exists():
            self._log("skip", "review", name)
            return
        payload = dict(item)
        product_slug = payload.pop("product", None)
        status = payload.pop("status", ReviewStatus.APPROVED)
        if product_slug:
            product = Product.objects.filter(slug=product_slug).first()
            if product is None:
                raise CommandError(f"review by '{name}' uses unknown product: {product_slug}")
            payload["product_id"] = str(product.pk)

        from reviews.api.v1.serializers import AdminReviewSerializer

        serializer = AdminReviewSerializer(data=payload)
        self._validate(serializer, f"review by '{name}'")
        data = dict(serializer.validated_data)
        data.pop("status", None)
        review = Review(source=ReviewSource.STAFF, **data)
        apply_status(review, status)
        review.save()
        self._log("create", "review", name)

    # -- helpers ----------------------------------------------------------

    def _validate(self, serializer, label):
        if not serializer.is_valid():
            raise CommandError(f"Invalid {label}: {json.dumps(serializer.errors, default=str)}")

    def _log(self, action, label, name):
        if action == "create":
            self.counts["created"] += 1
            self.stdout.write(f"  + {label}: {name}")
        else:
            self.counts["skipped"] += 1
            self.stdout.write(f"  = {label}: {name} (exists)")
