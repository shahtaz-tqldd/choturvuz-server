from django.core.exceptions import ValidationError
from django.db.models import Q

from catalog.api.v1.queries import parse_bool

REVIEW_ORDERINGS = {
    "newest": ("-published_at", "-created_at"),
    "oldest": ("published_at", "created_at"),
    "highest": ("-rating", "-published_at"),
    "lowest": ("rating", "-published_at"),
}


def _ints(value):
    return [int(item) for item in (value or "").split(",") if item.strip().isdigit()]


def filter_reviews(queryset, params):
    """Filters shared by the public and admin review lists."""
    ratings = [rating for rating in _ints(params.get("rating")) if 1 <= rating <= 5]
    if ratings:
        queryset = queryset.filter(rating__in=ratings)

    min_rating = _ints(params.get("min_rating"))
    if min_rating:
        queryset = queryset.filter(rating__gte=min_rating[0])

    featured = parse_bool(params.get("featured"))
    if featured is not None:
        queryset = queryset.filter(is_featured=featured)

    verified = parse_bool(params.get("verified"))
    if verified is not None:
        queryset = queryset.filter(order__isnull=not verified)

    product = (params.get("product") or "").strip()
    if product:
        lookup = Q(product__slug=product)
        try:
            from uuid import UUID

            lookup |= Q(product__pk=UUID(product))
        except (ValueError, ValidationError):
            pass
        queryset = queryset.filter(lookup)
    return queryset
