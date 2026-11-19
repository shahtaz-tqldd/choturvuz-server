from decimal import Decimal, InvalidOperation

from django.db.models import (
    Case,
    DecimalField,
    Exists,
    F,
    OuterRef,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from catalog.models import ProductVariant

TRUE_VALUES = ("true", "1", "yes")
FALSE_VALUES = ("false", "0", "no")

# Product-level price after a running sale (variant price overrides are
# ignored, matching the card's effective_price).
EFFECTIVE_PRICE = Case(
    When(
        sale_price__isnull=False,
        sale_price__lt=F("base_price"),
        then=F("sale_price"),
    ),
    default=F("base_price"),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)

ON_SALE = Q(sale_price__isnull=False, sale_price__lt=F("base_price"))


def in_stock_exists():
    return Exists(
        ProductVariant.objects.filter(
            product=OuterRef("pk"), is_active=True, stock__gt=0
        )
    )


def with_price_and_stock(queryset):
    return queryset.annotate(
        price_effective=EFFECTIVE_PRICE,
        in_stock_annotation=in_stock_exists(),
    )


def with_total_stock(queryset):
    return queryset.annotate(
        total_stock=Coalesce(
            Sum("variants__stock", filter=Q(variants__is_active=True)), Value(0)
        )
    )


def parse_bool(value):
    value = (value or "").strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    return None


def parse_decimal(value):
    try:
        return Decimal(str(value).strip()) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def parse_list(value, upper=False):
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return [item.upper() for item in items] if upper else items


def apply_common_filters(queryset, params):
    """Filters shared by the storefront and admin product listings.

    Expects `with_price_and_stock` to have been applied.
    """
    on_sale = parse_bool(params.get("on_sale"))
    if on_sale is True:
        queryset = queryset.filter(ON_SALE)
    elif on_sale is False:
        queryset = queryset.exclude(ON_SALE)

    in_stock = parse_bool(params.get("in_stock"))
    if in_stock is not None:
        queryset = queryset.filter(in_stock_annotation=in_stock)

    sizes = parse_list(params.get("size"), upper=True)
    if sizes:
        variant_filter = Q(product=OuterRef("pk"), is_active=True, size__in=sizes)
        if in_stock:
            variant_filter &= Q(stock__gt=0)
        queryset = queryset.filter(Exists(ProductVariant.objects.filter(variant_filter)))

    min_price = parse_decimal(params.get("min_price"))
    if min_price is not None:
        queryset = queryset.filter(price_effective__gte=min_price)
    max_price = parse_decimal(params.get("max_price"))
    if max_price is not None:
        queryset = queryset.filter(price_effective__lte=max_price)

    collection = (params.get("collection") or "").strip()
    if collection:
        queryset = queryset.filter(
            Q(collections__slug=collection) | Q(collections__name__iexact=collection)
        )

    gender = (params.get("gender") or "").strip().upper()
    if gender:
        queryset = queryset.filter(gender=gender)

    return queryset


def apply_ordering(queryset, ordering, allowed, default="-created_at"):
    """`allowed` maps public ordering names to model/annotation fields."""
    ordering = (ordering or "").strip()
    field = allowed.get(ordering) or allowed.get(default) or default
    return queryset.order_by(field, "-created_at", "pk")
