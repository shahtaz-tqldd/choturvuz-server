from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from app.core.models import BaseModel
from catalog.utils.choices import (
    CollectionType,
    Gender,
    ProductKind,
    ProductSize,
    ProductStatus,
    StockMovementReason,
)


class Collection(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    name_bn = models.CharField(max_length=120, blank=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    type = models.CharField(
        max_length=12,
        choices=CollectionType.choices,
        default=CollectionType.CATEGORY,
        db_index=True,
    )
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(BaseModel):
    name = models.CharField(max_length=200)
    name_bn = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    highlights = models.JSONField(default=list, blank=True)
    fabric = models.CharField(max_length=120, blank=True)
    care_instructions = models.TextField(blank=True)
    gender = models.CharField(
        max_length=10, choices=Gender.choices, default=Gender.WOMEN
    )
    status = models.CharField(
        max_length=10, choices=ProductStatus.choices, default=ProductStatus.DRAFT
    )
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    sale_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    is_featured = models.BooleanField(default=False)
    collections = models.ManyToManyField(
        Collection, blank=True, related_name="products"
    )

    # Custom order templates (kind=CUSTOM). base_price / sale_price are the
    # starting price per piece; staff send the final quote per order.
    kind = models.CharField(
        max_length=10,
        choices=ProductKind.choices,
        default=ProductKind.REGULAR,
        db_index=True,
    )
    design_code = models.CharField(
        max_length=40,
        blank=True,
        help_text="Reference code customers quote for a custom design, e.g. JR-024.",
    )
    min_order_quantity = models.PositiveIntegerField(default=1)
    # Sizes a custom template can be made in (ProductSize values).
    custom_sizes = models.JSONField(default=list, blank=True)
    # Colours a custom template can be made in: [{"name": "Navy", "hex": "#1f2a44"}].
    custom_colors = models.JSONField(default=list, blank=True)
    size_chart_url = models.URLField(max_length=1024, blank=True)
    production_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Typical production time after confirmation."
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["design_code"],
                condition=~Q(design_code=""),
                name="unique_product_design_code",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_custom(self):
        return self.kind == ProductKind.CUSTOM

    @property
    def effective_price(self):
        """Price after any running sale; falls back to the base price."""
        if self.sale_price is not None and self.sale_price < self.base_price:
            return self.sale_price
        return self.base_price

    @property
    def discount_percent(self):
        if self.sale_price is None or self.sale_price >= self.base_price:
            return 0
        return int(
            round((self.base_price - self.sale_price) / self.base_price * 100)
        )


class ProductImage(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    image_url = models.URLField()
    alt_text = models.CharField(max_length=200, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]


class ProductVariant(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    size = models.CharField(
        max_length=12, choices=ProductSize.choices, default=ProductSize.M
    )
    color_name = models.CharField(max_length=60, blank=True)
    color_hex = models.CharField(max_length=7, blank=True)
    sku = models.CharField(max_length=64, unique=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overrides the product price when set.",
    )
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product", "size", "color_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "size", "color_name"],
                name="unique_product_variant_combination",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.display_name} ({self.sku})"

    @property
    def display_name(self):
        return " / ".join(part for part in (self.color_name, self.get_size_display()) if part)

    @property
    def effective_price(self):
        if self.price is not None:
            return self.price
        return self.product.effective_price


class StockMovement(BaseModel):
    """Ledger of every stock change on a variant (orders, cancels, edits)."""

    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="stock_movements"
    )
    delta = models.IntegerField()
    stock_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=StockMovementReason.choices)
    note = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["variant", "-created_at"])]

    def __str__(self):
        return f"{self.variant.sku} {self.delta:+d} ({self.reason})"
