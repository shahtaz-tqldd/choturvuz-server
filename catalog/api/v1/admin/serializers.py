from decimal import Decimal

from django.db.models import ProtectedError, Sum
from django.utils import timezone
from rest_framework import serializers

from catalog.api.v1.serializers import (
    CollectionSerializer,
    ProductImageSerializer,
    ProductVariantSerializer,
)
from catalog.models import Collection, Product, ProductVariant, StockMovement
from catalog.services.inventory import InsufficientStockError, apply_stock_changes
from catalog.utils.choices import (
    ProductKind,
    ProductSize,
    ProductStatus,
    StockMovementReason,
)


class ProductVariantWriteSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(required=False)

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "size",
            "color_name",
            "color_hex",
            "sku",
            "price",
            "stock",
            "is_active",
        )
        # SKU uniqueness is checked by ProductWriteSerializer, which knows
        # which existing variant each line updates.
        extra_kwargs = {"sku": {"validators": []}}

    def validate(self, attrs):
        size = attrs.get("size", getattr(self.instance, "size", None))
        color_name = attrs.get(
            "color_name", getattr(self.instance, "color_name", "")
        )
        product = None
        if self.instance is not None:
            product = self.instance.product
        elif self.parent is not None and self.parent.instance is not None:
            product = self.parent.instance

        if product is not None:
            exists = (
                ProductVariant.objects.filter(
                    product=product,
                    size=size,
                    color_name=color_name,
                )
                .exclude(pk=getattr(self.instance, "pk", None))
                .exists()
            )
            if exists:
                raise serializers.ValidationError(
                    "A variant with this size and color already exists for the product."
                )
        return attrs


class CustomColorSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=60)
    hex = serializers.RegexField(
        r"^#[0-9A-Fa-f]{6}$", required=False, allow_blank=True, default=""
    )


class ProductWriteSerializer(serializers.ModelSerializer):
    variants = ProductVariantWriteSerializer(many=True, required=False)
    custom_sizes = serializers.ListField(
        child=serializers.ChoiceField(choices=ProductSize.choices),
        required=False,
        max_length=len(ProductSize.values),
    )
    custom_colors = CustomColorSerializer(many=True, required=False)
    min_order_quantity = serializers.IntegerField(min_value=1, required=False)
    images = ProductImageSerializer(many=True, required=False)
    collection_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
    )
    effective_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    highlights = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=20
    )
    total_stock = serializers.SerializerMethodField()
    # Returned on every read. Send it back unchanged with a PATCH that
    # includes `variants`: stock values you did not edit are then left alone,
    # and edited ones are applied on top of any orders placed in between.
    stock_snapshot_at = serializers.DateTimeField(required=False, write_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "name_bn",
            "slug",
            "description",
            "highlights",
            "fabric",
            "care_instructions",
            "gender",
            "status",
            "base_price",
            "sale_price",
            "is_featured",
            "kind",
            "design_code",
            "min_order_quantity",
            "custom_sizes",
            "custom_colors",
            "size_chart_url",
            "production_days",
            "collections",
            "collection_ids",
            "variants",
            "images",
            "effective_price",
            "total_stock",
            "stock_snapshot_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "slug", "created_at", "updated_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["stock_snapshot_at"] = timezone.now().isoformat()
        return data

    def get_total_stock(self, product):
        return sum(
            variant.stock for variant in product.variants.all() if variant.is_active
        )

    def validate_design_code(self, value):
        value = (value or "").strip().upper()
        if value:
            clash = Product.objects.filter(design_code__iexact=value)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError("This design code is already in use.")
        return value

    def validate_custom_sizes(self, value):
        # Keep the canonical size order, drop duplicates.
        chosen = set(value)
        return [size for size in ProductSize.values if size in chosen]

    def validate_custom_colors(self, value):
        names = [color["name"].strip().casefold() for color in value]
        if len(names) != len(set(names)):
            raise serializers.ValidationError("Colour names must be unique.")
        return [
            {"name": color["name"].strip(), "hex": (color.get("hex") or "").lower()}
            for color in value
        ]

    def validate_collection_ids(self, value):
        collections = Collection.objects.filter(id__in=value)
        if collections.count() != len(set(value)):
            found = set(collections.values_list("id", flat=True))
            missing = [str(item) for item in set(value) - found]
            raise serializers.ValidationError(
                f"Unknown collection id(s): {', '.join(missing)}."
            )
        return value

    def validate(self, attrs):
        base_price = attrs.get(
            "base_price", getattr(self.instance, "base_price", None)
        )
        sale_price = attrs.get(
            "sale_price", getattr(self.instance, "sale_price", None)
        )
        if (
            sale_price is not None
            and base_price is not None
            and sale_price > base_price
        ):
            raise serializers.ValidationError(
                {"sale_price": "Sale price cannot exceed the base price."}
            )

        kind = attrs.get("kind", getattr(self.instance, "kind", ProductKind.REGULAR))
        variants = attrs.get("variants")
        if kind == ProductKind.CUSTOM:
            if variants:
                raise serializers.ValidationError(
                    {
                        "variants": [
                            "Custom templates have no variants. Use custom_sizes "
                            "and custom_colors instead."
                        ]
                    }
                )
            if (
                self.instance is not None
                and self.instance.kind != ProductKind.CUSTOM
                and variants is None
                and self.instance.variants.exists()
            ):
                raise serializers.ValidationError(
                    {
                        "kind": [
                            "Remove this product's variants (send variants: []) "
                            "before turning it into a custom template."
                        ]
                    }
                )

        if variants is not None:
            combos = [
                (v.get("size"), (v.get("color_name") or "")) for v in variants
            ]
            if len(combos) != len(set(combos)):
                raise serializers.ValidationError(
                    {
                        "variants": [
                            "Duplicate size and color combinations are not allowed."
                        ]
                    }
                )
            skus = [v.get("sku") for v in variants]
            if len(skus) != len(set(skus)):
                raise serializers.ValidationError(
                    {"variants": ["Duplicate SKUs are not allowed."]}
                )
            self._validate_sku_ownership(variants)
        return attrs

    def _validate_sku_ownership(self, variants):
        """A SKU may only be reused by the variant that already owns it, or
        by a variant of this product that the update removes."""
        kept_ids = {str(v["id"]) for v in variants if v.get("id")}
        taken = []
        for variant in variants:
            owners = ProductVariant.objects.filter(sku=variant.get("sku"))
            if variant.get("id") and self.instance is not None:
                # Only an id of this product's own variant updates in place.
                owners = owners.exclude(pk=variant["id"], product=self.instance)
            for owner in owners:
                same_product = self.instance is not None and owner.product_id == self.instance.pk
                if same_product and str(owner.pk) not in kept_ids:
                    continue  # will be deleted before this line is saved
                taken.append(variant.get("sku"))
        if taken:
            raise serializers.ValidationError(
                {"variants": [f"SKU already in use: {', '.join(sorted(set(taken)))}."]}
            )

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def create(self, validated_data):
        variants_data = validated_data.pop("variants", None)
        images_data = validated_data.pop("images", None)
        collection_ids = validated_data.pop("collection_ids", None)
        validated_data.pop("stock_snapshot_at", None)
        product = Product.objects.create(**validated_data)
        self._apply_nested(
            product,
            variants_data=variants_data,
            images_data=images_data,
            collection_ids=collection_ids,
        )
        return product

    def update(self, instance, validated_data):
        variants_data = validated_data.pop("variants", None)
        images_data = validated_data.pop("images", None)
        collection_ids = validated_data.pop("collection_ids", None)
        stock_snapshot_at = validated_data.pop("stock_snapshot_at", None)
        instance = super().update(instance, validated_data)
        self._apply_nested(
            instance,
            variants_data=variants_data,
            images_data=images_data,
            collection_ids=collection_ids,
            stock_snapshot_at=stock_snapshot_at,
        )
        return instance

    def _apply_nested(
        self,
        product,
        *,
        variants_data=None,
        images_data=None,
        collection_ids=None,
        stock_snapshot_at=None,
    ):
        if collection_ids is not None:
            product.collections.set(
                Collection.objects.filter(id__in=collection_ids)
            )

        if images_data is not None:
            product.images.all().delete()
            for image_data in images_data:
                product.images.create(
                    image_url=image_data["image_url"],
                    alt_text=image_data.get("alt_text", ""),
                    position=image_data.get("position", 0),
                )

        if variants_data is not None:
            self._apply_variants(product, variants_data, stock_snapshot_at)

        # Drop prefetched relations so the response reflects the changes.
        if hasattr(product, "_prefetched_objects_cache"):
            product._prefetched_objects_cache = {}
        return product

    def _apply_variants(self, product, variants_data, stock_snapshot_at):
        user = self._user()
        existing_variants = {str(v.pk): v for v in product.variants.all()}
        kept_ids = {
            str(variant_data.get("id"))
            for variant_data in variants_data
            if variant_data.get("id")
        }

        # Remove lines the admin dropped before adding replacements,
        # so identical size/color pairs don't collide mid-update.
        for variant_id, variant in existing_variants.items():
            if variant_id in kept_ids:
                continue
            try:
                variant.delete()
            except ProtectedError:
                raise serializers.ValidationError(
                    {
                        "variants": [
                            f"Variant {variant.sku} has orders and cannot be removed. "
                            "Mark it inactive instead."
                        ]
                    }
                )

        stock_changes = {}
        for variant_data in variants_data:
            variant_data = dict(variant_data)
            variant_id = str(variant_data.pop("id", "") or "")
            requested_stock = variant_data.pop("stock", None)
            variant = existing_variants.get(variant_id) if variant_id else None

            if variant is None:
                stock = requested_stock or 0
                variant = product.variants.create(stock=stock, **variant_data)
                if stock:
                    StockMovement.objects.create(
                        variant=variant,
                        delta=stock,
                        stock_after=stock,
                        reason=StockMovementReason.INITIAL,
                        created_by=user if getattr(user, "is_authenticated", False) else None,
                    )
                continue

            for field, value in variant_data.items():
                setattr(variant, field, value)
            # Never write `stock` from this (possibly stale) instance.
            if variant_data:
                variant.save(update_fields=list(variant_data) + ["updated_at"])

            if requested_stock is not None:
                delta = self._stock_delta(variant, requested_stock, stock_snapshot_at)
                if delta:
                    stock_changes[variant.pk] = delta

        if stock_changes:
            try:
                apply_stock_changes(
                    stock_changes,
                    reason=StockMovementReason.PRODUCT_EDIT,
                    user=user,
                    note="Product edit",
                )
            except InsufficientStockError as exc:
                raise serializers.ValidationError({"variants": [str(exc)]})

    @staticmethod
    def _stock_delta(variant, requested_stock, stock_snapshot_at):
        """How much to change stock so the admin's edit is applied safely."""
        current = (
            ProductVariant.objects.filter(pk=variant.pk)
            .values_list("stock", flat=True)
            .first()
        )
        if stock_snapshot_at is None:
            # Legacy clients: the sent value wins (still locked and logged).
            return requested_stock - current
        moved_since = (
            StockMovement.objects.filter(
                variant=variant, created_at__gt=stock_snapshot_at
            ).aggregate(total=Sum("delta"))["total"]
            or 0
        )
        seen_by_client = current - moved_since
        if requested_stock == seen_by_client:
            return 0  # not edited; keep whatever orders did since
        return requested_stock - seen_by_client


class VariantStockUpdateSerializer(serializers.ModelSerializer):
    """PATCH a single variant: stock (absolute) and simple fields."""

    stock = serializers.IntegerField(min_value=0, required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    class Meta:
        model = ProductVariant
        fields = ("stock", "price", "is_active", "color_hex", "note")


class VariantStockAdjustSerializer(serializers.Serializer):
    delta = serializers.IntegerField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("delta cannot be zero.")
        return value


class BulkStockItemSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=64)
    stock = serializers.IntegerField(min_value=0)


class BulkStockSerializer(serializers.Serializer):
    """Accepts either a bare list or {"items": [...], "note": "..."}."""

    items = BulkStockItemSerializer(many=True, allow_empty=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def to_internal_value(self, data):
        if isinstance(data, list):
            data = {"items": data}
        return super().to_internal_value(data)

    def validate_items(self, items):
        skus = [item["sku"] for item in items]
        duplicates = sorted({sku for sku in skus if skus.count(sku) > 1})
        if duplicates:
            raise serializers.ValidationError(f"Duplicate SKU(s): {', '.join(duplicates)}.")
        if len(items) > 5000:
            raise serializers.ValidationError("Send at most 5000 rows per request.")
        return items


class StockMovementSerializer(serializers.ModelSerializer):
    variant_id = serializers.UUIDField(source="variant.pk", read_only=True)
    sku = serializers.CharField(source="variant.sku", read_only=True)
    product_id = serializers.UUIDField(source="variant.product_id", read_only=True)
    product_name = serializers.CharField(source="variant.product.name", read_only=True)
    display_name = serializers.CharField(source="variant.display_name", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True, default=None)
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = (
            "id",
            "variant_id",
            "sku",
            "product_id",
            "product_name",
            "display_name",
            "delta",
            "stock_after",
            "reason",
            "note",
            "order_number",
            "created_by",
            "created_at",
        )

    def get_created_by(self, movement):
        user = movement.created_by
        if user is None:
            return None
        return {"id": str(user.pk), "name": user.name, "email": user.email}


BULK_PRODUCT_ACTIONS = (
    "set_status",
    "feature",
    "unfeature",
    "set_sale_percent",
    "clear_sale",
    "delete",
)


class ProductBulkActionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=500)
    action = serializers.ChoiceField(choices=BULK_PRODUCT_ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        action = attrs["action"]
        value = attrs.get("value")
        if action == "set_status":
            if value not in ProductStatus.values:
                raise serializers.ValidationError(
                    {"value": [f"Choose one of: {', '.join(ProductStatus.values)}."]}
                )
        elif action == "set_sale_percent":
            try:
                percent = Decimal(str(value))
            except Exception:
                percent = None
            if percent is None or not (Decimal("0") < percent < Decimal("100")):
                raise serializers.ValidationError(
                    {"value": ["Send a percentage between 0 and 100 (exclusive)."]}
                )
            attrs["value"] = percent
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs


class CollectionWriteSerializer(CollectionSerializer):
    product_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
    )

    class Meta(CollectionSerializer.Meta):
        fields = CollectionSerializer.Meta.fields + ("product_ids",)

    def validate_product_ids(self, value):
        products = Product.objects.filter(id__in=value)
        if products.count() != len(set(value)):
            found = set(products.values_list("id", flat=True))
            missing = [str(item) for item in set(value) - found]
            raise serializers.ValidationError(
                f"Unknown product id(s): {', '.join(missing)}."
            )
        return value

    def create(self, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        collection = Collection.objects.create(**validated_data)
        if product_ids is not None:
            collection.products.set(
                Product.objects.filter(id__in=product_ids)
            )
        return collection

    def update(self, instance, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        instance = super().update(instance, validated_data)
        if product_ids is not None:
            instance.products.set(Product.objects.filter(id__in=product_ids))
        return instance


class CollectionProductAssignSerializer(serializers.Serializer):
    """Assign or unassign products to/from a collection."""

    action = serializers.ChoiceField(choices=("add", "remove", "set"))
    product_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True
    )

    def validate_product_ids(self, value):
        if not value:
            return value
        products = Product.objects.filter(id__in=value)
        if products.count() != len(set(value)):
            found = set(products.values_list("id", flat=True))
            missing = [str(item) for item in set(value) - found]
            raise serializers.ValidationError(
                f"Unknown product id(s): {', '.join(missing)}."
            )
        return value
