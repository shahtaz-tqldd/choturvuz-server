from rest_framework import serializers

from catalog.models import Collection, Product, ProductImage, ProductVariant
from catalog.utils.choices import ProductKind


class CollectionSerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)
    custom_template_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Collection
        fields = (
            "id",
            "name",
            "name_bn",
            "slug",
            "description",
            "image_url",
            "type",
            "position",
            "is_active",
            "product_count",
            "custom_template_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "slug", "created_at", "updated_at")


class ProductVariantSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    effective_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    is_in_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "size",
            "color_name",
            "color_hex",
            "display_name",
            "sku",
            "price",
            "effective_price",
            "stock",
            "is_in_stock",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_is_in_stock(self, variant):
        return variant.stock > 0


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("id", "image_url", "alt_text", "position")
        read_only_fields = ("id",)


def product_images(product):
    # Uses the prefetch cache when present (ordered by position).
    return list(product.images.all())


def product_in_stock(product):
    if product.kind == ProductKind.CUSTOM:
        return True  # made to order
    annotated = getattr(product, "in_stock_annotation", None)
    if annotated is not None:
        return bool(annotated)
    return any(
        variant.is_active and variant.stock > 0 for variant in product.variants.all()
    )


class ProductCardSerializer(serializers.ModelSerializer):
    """Lightweight product payload for grids and carousels."""

    effective_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    discount_percent = serializers.IntegerField(read_only=True)
    primary_image = serializers.SerializerMethodField()
    secondary_image = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    collections = serializers.SlugRelatedField(
        many=True, slug_field="slug", read_only=True
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "name_bn",
            "slug",
            "kind",
            "design_code",
            "min_order_quantity",
            "gender",
            "fabric",
            "status",
            "base_price",
            "sale_price",
            "effective_price",
            "discount_percent",
            "is_featured",
            "in_stock",
            "primary_image",
            "secondary_image",
            "collections",
            "created_at",
        )

    def get_primary_image(self, product):
        images = product_images(product)
        return images[0].image_url if images else ""

    def get_secondary_image(self, product):
        images = product_images(product)
        return images[1].image_url if len(images) > 1 else ""

    def get_in_stock(self, product):
        return product_in_stock(product)


class ProductDetailSerializer(ProductCardSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = serializers.SerializerMethodField()

    class Meta(ProductCardSerializer.Meta):
        fields = ProductCardSerializer.Meta.fields + (
            "description",
            "highlights",
            "care_instructions",
            "custom_sizes",
            "custom_colors",
            "size_chart_url",
            "production_days",
            "images",
            "variants",
            "updated_at",
        )

    def get_variants(self, product):
        variants = [variant for variant in product.variants.all() if variant.is_active]
        return ProductVariantSerializer(variants, many=True).data
