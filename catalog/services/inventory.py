"""All variant stock changes go through here.

Every change locks the variant rows (SELECT ... FOR UPDATE, in primary-key
order to avoid deadlocks), refuses to go below zero, updates with F() and
writes a StockMovement row, so stock history is complete and concurrent
orders cannot oversell.
"""

from django.db import transaction
from django.db.models import F

from app.services.revalidate import PRODUCTS, product_tag, queue_revalidation
from catalog.models import ProductVariant, StockMovement
from catalog.utils.choices import StockMovementReason


class InsufficientStockError(ValueError):
    """Raised when a change would take a variant's stock below zero."""

    def __init__(self, message, shortages=None):
        super().__init__(message)
        self.shortages = shortages or []


def _shortage_message(shortages):
    first = shortages[0]
    variant = first["variant"]
    return (
        f"{variant.product.name} ({variant.display_name}) only has "
        f"{first['available']} left in stock."
    )


@transaction.atomic
def apply_stock_changes(
    changes,
    *,
    reason,
    order=None,
    user=None,
    note="",
):
    """Apply stock deltas atomically.

    `changes` maps variant id -> delta (negative takes stock away). Returns a
    dict of variant id (str) -> new stock. Raises InsufficientStockError and
    changes nothing when any variant would go below zero.
    """
    deltas = {}
    for variant_id, delta in dict(changes).items():
        if delta:
            deltas[str(variant_id)] = deltas.get(str(variant_id), 0) + int(delta)
    deltas = {key: value for key, value in deltas.items() if value}
    if not deltas:
        return {}

    variants = list(
        ProductVariant.objects.select_for_update(of=("self",))
        .select_related("product")
        .filter(pk__in=list(deltas))
        .order_by("pk")
    )
    found = {str(variant.pk): variant for variant in variants}
    missing = set(deltas) - set(found)
    if missing:
        raise ProductVariant.DoesNotExist(
            f"Unknown variant id(s): {', '.join(sorted(missing))}."
        )

    shortages = [
        {"variant": variant, "requested": -deltas[key], "available": variant.stock}
        for key, variant in found.items()
        if variant.stock + deltas[key] < 0
    ]
    if shortages:
        raise InsufficientStockError(_shortage_message(shortages), shortages)

    result = {}
    tags = set()
    movements = []
    for key, variant in found.items():
        delta = deltas[key]
        before = variant.stock
        after = before + delta
        ProductVariant.objects.filter(pk=variant.pk).update(stock=F("stock") + delta)
        variant.stock = after
        result[key] = after
        movements.append(
            StockMovement(
                variant=variant,
                delta=delta,
                stock_after=after,
                reason=reason,
                note=(note or "")[:255],
                order=order,
                created_by=user if getattr(user, "is_authenticated", False) else None,
            )
        )
        tags.add(product_tag(variant.product.slug))
        if (before > 0) != (after > 0):
            # A product card's "sold out" state may have changed.
            tags.add(PRODUCTS)

    StockMovement.objects.bulk_create(movements)
    queue_revalidation(*tags)
    return result


def set_stock(variant_id, stock, *, reason=StockMovementReason.SET, user=None, note=""):
    """Set an absolute stock value (recorded as a delta)."""
    return set_stocks({variant_id: stock}, reason=reason, user=user, note=note)


@transaction.atomic
def set_stocks(targets, *, reason=StockMovementReason.SET, user=None, note=""):
    """Set absolute stock values for several variants in one transaction."""
    targets = {str(key): int(value) for key, value in dict(targets).items()}
    if any(value < 0 for value in targets.values()):
        raise ValueError("Stock cannot be negative.")
    current = dict(
        ProductVariant.objects.select_for_update(of=("self",))
        .filter(pk__in=list(targets))
        .order_by("pk")
        .values_list("pk", "stock")
    )
    current = {str(key): value for key, value in current.items()}
    missing = set(targets) - set(current)
    if missing:
        raise ProductVariant.DoesNotExist(
            f"Unknown variant id(s): {', '.join(sorted(missing))}."
        )
    changes = {key: targets[key] - current[key] for key in targets}
    apply_stock_changes(changes, reason=reason, user=user, note=note)
    return {key: targets[key] for key in targets}
