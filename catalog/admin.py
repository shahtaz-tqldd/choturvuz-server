from django.contrib import admin

from catalog.models import (
    Collection,
    Product,
    ProductImage,
    ProductVariant,
    StockMovement,
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kind",
        "design_code",
        "gender",
        "status",
        "base_price",
        "sale_price",
        "is_featured",
        "created_at",
    )
    list_filter = ("kind", "status", "gender", "is_featured", "collections")
    search_fields = ("name", "slug", "design_code", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ProductImageInline, ProductVariantInline)
    filter_horizontal = ("collections",)


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "size", "color_name", "stock", "is_active")
    list_filter = ("size", "is_active")
    search_fields = ("sku", "product__name", "color_name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("variant", "delta", "stock_after", "reason", "order", "created_by", "created_at")
    list_filter = ("reason",)
    search_fields = ("variant__sku", "variant__product__name", "order__order_number")
    readonly_fields = tuple(f.name for f in StockMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
