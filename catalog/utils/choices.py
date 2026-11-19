from django.db import models
from django.utils.translation import gettext_lazy as _


class Gender(models.TextChoices):
    MEN = "MEN", _("Men")
    WOMEN = "WOMEN", _("Women")
    UNISEX = "UNISEX", _("Unisex")
    KIDS = "KIDS", _("Kids")


class ProductSize(models.TextChoices):
    XS = "XS", _("XS")
    S = "S", _("S")
    M = "M", _("M")
    L = "L", _("L")
    XL = "XL", _("XL")
    XXL = "XXL", _("XXL")
    XXXL = "XXXL", _("3XL")
    ONE_SIZE = "ONE_SIZE", _("One Size")


class ProductKind(models.TextChoices):
    # Ready-made product with stock-tracked variants.
    REGULAR = "REGULAR", _("Regular")
    # Design template customers order with their own artwork, sizes and
    # quantities. No variants or stock; priced per piece, quoted by staff.
    CUSTOM = "CUSTOM", _("Custom order template")


class ProductStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    ACTIVE = "ACTIVE", _("Active")
    ARCHIVED = "ARCHIVED", _("Archived")


class CollectionType(models.TextChoices):
    CATEGORY = "CATEGORY", _("Category")
    FESTIVE = "FESTIVE", _("Festive")
    EDIT = "EDIT", _("Edit")


class StockMovementReason(models.TextChoices):
    ORDER_PLACED = "ORDER_PLACED", _("Order placed")
    ORDER_CANCELLED = "ORDER_CANCELLED", _("Order cancelled")
    ADJUSTMENT = "ADJUSTMENT", _("Manual adjustment")
    SET = "SET", _("Stock set")
    IMPORT = "IMPORT", _("Import")
    PRODUCT_EDIT = "PRODUCT_EDIT", _("Product edit")
    INITIAL = "INITIAL", _("Initial stock")
