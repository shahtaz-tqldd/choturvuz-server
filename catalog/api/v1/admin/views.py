from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, ProtectedError, Q
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.services.revalidate import PRODUCTS, product_tag, queue_revalidation
from app.utils.pagination import CustomPagination
from app.utils.permission import IsAdmin
from app.utils.response import APIResponse
from base.models import StoreConfig
from catalog.api.v1.admin.serializers import (
    BulkStockSerializer,
    CollectionProductAssignSerializer,
    CollectionWriteSerializer,
    ProductBulkActionSerializer,
    ProductWriteSerializer,
    StockMovementSerializer,
    VariantStockAdjustSerializer,
    VariantStockUpdateSerializer,
)
from catalog.api.v1.queries import (
    apply_common_filters,
    apply_ordering,
    parse_bool,
    with_price_and_stock,
    with_total_stock,
)
from catalog.api.v1.serializers import ProductVariantSerializer
from catalog.models import Collection, Product, ProductVariant, StockMovement
from catalog.services.inventory import (
    InsufficientStockError,
    apply_stock_changes,
    set_stocks,
)
from catalog.utils.choices import StockMovementReason

ADMIN_PRODUCT_ORDERINGS = {
    "created_at": "created_at",
    "-created_at": "-created_at",
    "updated_at": "updated_at",
    "-updated_at": "-updated_at",
    "name": "name",
    "-name": "-name",
    "base_price": "base_price",
    "-base_price": "-base_price",
    "effective_price": "price_effective",
    "-effective_price": "-price_effective",
    "stock": "total_stock",
    "-stock": "-total_stock",
}


def low_stock_threshold(params=None):
    value = (params or {}).get("low_stock_threshold", "")
    if str(value).strip().isdigit():
        return int(value)
    config = StoreConfig.objects.first()
    return config.low_stock_threshold if config else 3


def _meta(paginator, request):
    return {
        "count": paginator.page.paginator.count,
        "page": paginator.page.number,
        "page_size": paginator.get_page_size(request),
        "num_pages": paginator.page.paginator.num_pages,
        "next": paginator.get_next_link(),
        "previous": paginator.get_previous_link(),
    }


class AdminProductListAPIView(GenericAPIView):
    """List all products (any status) with filters, or create one."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = ProductWriteSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = with_total_stock(
            with_price_and_stock(Product.objects.all())
        ).prefetch_related("variants", "images", "collections")

        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            sku_match = ProductVariant.objects.filter(
                product=OuterRef("pk"), sku__icontains=search
            )
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(name_bn__icontains=search)
                | Q(slug__icontains=search)
                | Q(design_code__icontains=search)
                | Exists(sku_match)
            )

        kinds = [v.strip().upper() for v in params.get("kind", "").split(",") if v.strip()]
        if kinds and "ALL" not in kinds:
            queryset = queryset.filter(kind__in=kinds)

        statuses = [v.strip().upper() for v in params.get("status", "").split(",") if v.strip()]
        if statuses:
            queryset = queryset.filter(status__in=statuses)

        featured = parse_bool(params.get("is_featured") or params.get("featured"))
        if featured is not None:
            queryset = queryset.filter(is_featured=featured)

        stock_filter = params.get("stock", "").strip().lower()
        if stock_filter == "out":
            # No active variant has any stock left.
            queryset = queryset.filter(in_stock_annotation=False)
        elif stock_filter == "low":
            threshold = low_stock_threshold(params)
            queryset = queryset.filter(
                Exists(
                    ProductVariant.objects.filter(
                        product=OuterRef("pk"), is_active=True, stock__lte=threshold
                    )
                )
            )

        queryset = apply_common_filters(queryset, params)
        queryset = apply_ordering(
            queryset, params.get("ordering", "-created_at"), ADMIN_PRODUCT_ORDERINGS
        )
        return queryset.distinct()

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(
            self.get_queryset(), request, view=self
        )
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Products fetched successfully.",
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            product = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(product).data,
            message="Product created successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminProductDetailAPIView(GenericAPIView):
    """Retrieve, update, or delete a product by id."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = ProductWriteSerializer

    def get_queryset(self):
        return Product.objects.prefetch_related("variants", "images", "collections")

    def get(self, request, pk, *args, **kwargs):
        product = self.get_queryset().filter(pk=pk).first()
        if product is None:
            return APIResponse.error(message="Product not found.", status=404)
        return APIResponse.success(
            data=self.get_serializer(product).data,
            message="Product fetched successfully.",
        )

    def patch(self, request, pk, *args, **kwargs):
        product = self.get_queryset().filter(pk=pk).first()
        if product is None:
            return APIResponse.error(message="Product not found.", status=404)
        serializer = self.get_serializer(
            product, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            product = serializer.save()
        product = self.get_queryset().get(pk=product.pk)
        return APIResponse.success(
            data=self.get_serializer(product).data,
            message="Product updated successfully.",
        )

    def delete(self, request, pk, *args, **kwargs):
        product = self.get_queryset().filter(pk=pk).first()
        if product is None:
            return APIResponse.error(message="Product not found.", status=404)
        try:
            with transaction.atomic():
                product.delete()
        except ProtectedError:
            return APIResponse.error(
                message="This product has orders and cannot be deleted. Archive it instead.",
                status=status.HTTP_409_CONFLICT,
            )
        return APIResponse.success(message="Product deleted successfully.")


class AdminCollectionListAPIView(GenericAPIView):
    """List collections or create a new one (optionally with products)."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = CollectionWriteSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = Collection.objects.annotate(
            product_count=Count("products", distinct=True),
            custom_template_count=Count(
                "products", filter=Q(products__kind="CUSTOM"), distinct=True
            ),
        )
        params = self.request.query_params
        types = [v.strip().upper() for v in params.get("type", "").split(",") if v.strip()]
        if types:
            queryset = queryset.filter(type__in=types)
        ordering = params.get("ordering", "-created_at").strip()
        allowed = {"-created_at", "created_at", "position", "-position", "name", "-name"}
        if ordering not in allowed:
            ordering = "-created_at"
        return queryset.order_by(ordering, "name")

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(
            self.get_queryset(), request, view=self
        )
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Collections fetched successfully.",
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collection = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(collection).data,
            message="Collection created successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminCollectionDetailAPIView(GenericAPIView):
    """Retrieve, update, or delete a collection by id."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = CollectionWriteSerializer

    def get_queryset(self):
        return Collection.objects.annotate(product_count=Count("products"))

    def get(self, request, pk, *args, **kwargs):
        collection = self.get_queryset().filter(pk=pk).first()
        if collection is None:
            return APIResponse.error(message="Collection not found.", status=404)
        data = self.get_serializer(collection).data
        data["product_ids"] = list(
            collection.products.values_list("id", flat=True)
        )
        return APIResponse.success(
            data=data,
            message="Collection fetched successfully.",
        )

    def patch(self, request, pk, *args, **kwargs):
        collection = self.get_queryset().filter(pk=pk).first()
        if collection is None:
            return APIResponse.error(message="Collection not found.", status=404)
        serializer = self.get_serializer(
            collection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        collection = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(collection).data,
            message="Collection updated successfully.",
        )

    def delete(self, request, pk, *args, **kwargs):
        collection = self.get_queryset().filter(pk=pk).first()
        if collection is None:
            return APIResponse.error(message="Collection not found.", status=404)
        # Removing a collection never removes its products; the M2M rows go away.
        collection.delete()
        return APIResponse.success(message="Collection deleted successfully.")


class AdminCollectionProductsAPIView(GenericAPIView):
    """Assign or remove products to/from a collection."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = CollectionProductAssignSerializer

    def get_queryset(self):
        return Collection.objects.all()

    def post(self, request, pk, *args, **kwargs):
        collection = self.get_queryset().filter(pk=pk).first()
        if collection is None:
            return APIResponse.error(message="Collection not found.", status=404)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        product_ids = serializer.validated_data["product_ids"]

        if action == "set":
            collection.products.set(product_ids)
        elif action == "add":
            collection.products.add(*product_ids)
        else:
            collection.products.remove(*product_ids)

        return APIResponse.success(
            data={
                "collection_id": str(collection.pk),
                "product_ids": list(
                    collection.products.values_list("id", flat=True)
                ),
            },
            message="Collection products updated successfully.",
        )


def _variant_payload(variant):
    data = ProductVariantSerializer(variant).data
    data["product_id"] = str(variant.product_id)
    data["product_name"] = variant.product.name
    return data


class AdminVariantDetailAPIView(GenericAPIView):
    """Get or update one variant. `stock` here is an absolute value."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = VariantStockUpdateSerializer

    def _get(self, pk):
        return ProductVariant.objects.select_related("product").filter(pk=pk).first()

    def get(self, request, pk, *args, **kwargs):
        variant = self._get(pk)
        if variant is None:
            return APIResponse.error(message="Variant not found.", status=404)
        return APIResponse.success(data=_variant_payload(variant), message="Variant fetched successfully.")

    def patch(self, request, pk, *args, **kwargs):
        variant = self._get(pk)
        if variant is None:
            return APIResponse.error(message="Variant not found.", status=404)
        serializer = self.get_serializer(variant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        stock = data.pop("stock", None)
        note = data.pop("note", "")

        with transaction.atomic():
            if data:
                for field, value in data.items():
                    setattr(variant, field, value)
                variant.save(update_fields=list(data) + ["updated_at"])
            if stock is not None:
                set_stocks({variant.pk: stock}, reason=StockMovementReason.SET, user=request.user, note=note)

        return APIResponse.success(data=_variant_payload(self._get(pk)), message="Variant updated successfully.")


class AdminVariantAdjustStockAPIView(GenericAPIView):
    """Atomically add or remove stock (e.g. +10 restock, -2 damaged)."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = VariantStockAdjustSerializer

    def post(self, request, pk, *args, **kwargs):
        if not ProductVariant.objects.filter(pk=pk).exists():
            return APIResponse.error(message="Variant not found.", status=404)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            apply_stock_changes(
                {pk: serializer.validated_data["delta"]},
                reason=StockMovementReason.ADJUSTMENT,
                user=request.user,
                note=serializer.validated_data["reason"],
            )
        except InsufficientStockError as exc:
            return APIResponse.error(message=str(exc), status=status.HTTP_409_CONFLICT)
        variant = ProductVariant.objects.select_related("product").get(pk=pk)
        return APIResponse.success(data=_variant_payload(variant), message="Stock adjusted successfully.")


class AdminVariantBulkStockAPIView(GenericAPIView):
    """Set absolute stock for many SKUs in one transaction (CSV stock import).

    Unknown SKUs reject the whole request so nothing is half-applied.
    """

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = BulkStockSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["items"]
        note = serializer.validated_data["note"] or "Stock import"

        by_sku = dict(
            ProductVariant.objects.filter(sku__in=[item["sku"] for item in items]).values_list("sku", "pk")
        )
        unknown = [item["sku"] for item in items if item["sku"] not in by_sku]
        if unknown:
            return APIResponse.error(
                message=f"Unknown SKU(s): {', '.join(unknown[:20])}{'…' if len(unknown) > 20 else ''}",
                errors={"unknown_skus": unknown},
            )

        before = dict(ProductVariant.objects.filter(pk__in=by_sku.values()).values_list("pk", "stock"))
        set_stocks(
            {by_sku[item["sku"]]: item["stock"] for item in items},
            reason=StockMovementReason.IMPORT,
            user=request.user,
            note=note,
        )
        results = [
            {
                "sku": item["sku"],
                "variant_id": str(by_sku[item["sku"]]),
                "previous_stock": before[by_sku[item["sku"]]],
                "stock": item["stock"],
                "changed": before[by_sku[item["sku"]]] != item["stock"],
            }
            for item in items
        ]
        changed = sum(1 for row in results if row["changed"])
        return APIResponse.success(
            data={"results": results, "updated": changed, "unchanged": len(results) - changed},
            message=f"Stock updated for {changed} variant(s).",
        )


class AdminStockMovementListAPIView(GenericAPIView):
    """Stock history, filterable by variant, product, sku, reason or order."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = StockMovementSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = StockMovement.objects.select_related(
            "variant__product", "order", "created_by"
        ).order_by("-created_at")
        params = self.request.query_params
        filters = {
            "variant": "variant_id",
            "product": "variant__product_id",
            "sku": "variant__sku",
            "reason": "reason",
        }
        for param, field in filters.items():
            value = params.get(param, "").strip()
            if value:
                if param in ("variant", "product"):
                    try:
                        value = UUID(value)
                    except ValueError:
                        return queryset.none()
                queryset = queryset.filter(**{field: value})
        order_number = params.get("order_number", "").strip()
        if order_number:
            queryset = queryset.filter(order__order_number=order_number.upper())
        date_from = params.get("date_from", "").strip()
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        date_to = params.get("date_to", "").strip()
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return queryset

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(self.get_queryset(), request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta=_meta(paginator, request),
            message="Stock movements fetched successfully.",
        )


class AdminProductBulkActionAPIView(GenericAPIView):
    """Apply one action to many products, all or nothing."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = ProductBulkActionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]
        action = serializer.validated_data["action"]
        value = serializer.validated_data.get("value")

        products = Product.objects.filter(pk__in=ids)
        found = set(products.values_list("pk", flat=True))
        missing = [str(pk) for pk in ids if pk not in found]
        if missing:
            return APIResponse.error(
                message="Some products were not found.",
                errors={"ids": missing},
                status=404,
            )
        slugs = list(products.values_list("slug", flat=True))

        try:
            with transaction.atomic():
                if action == "set_status":
                    affected = products.update(status=value)
                elif action == "feature":
                    affected = products.update(is_featured=True)
                elif action == "unfeature":
                    affected = products.update(is_featured=False)
                elif action == "clear_sale":
                    affected = products.update(sale_price=None)
                elif action == "set_sale_percent":
                    affected = 0
                    factor = (Decimal("100") - value) / Decimal("100")
                    for product in products.select_for_update():
                        product.sale_price = (product.base_price * factor).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        product.save(update_fields=["sale_price", "updated_at"])
                        affected += 1
                else:  # delete
                    affected = len(ids)
                    for product in products:
                        product.delete()
                queue_revalidation(PRODUCTS, *(product_tag(slug) for slug in slugs))
        except ProtectedError:
            return APIResponse.error(
                message="Some products have orders and cannot be deleted. Archive them instead.",
                status=status.HTTP_409_CONFLICT,
            )

        return APIResponse.success(
            data={"action": action, "affected": affected, "ids": [str(pk) for pk in ids]},
            message=f"{affected} product(s) updated.",
        )
