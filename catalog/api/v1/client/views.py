from django.db.models import Count, Q
from django.core.exceptions import ValidationError
from rest_framework.generics import GenericAPIView

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from catalog.api.v1.serializers import (
    CollectionSerializer,
    ProductCardSerializer,
    ProductDetailSerializer,
)
from catalog.api.v1.queries import (
    apply_common_filters,
    apply_ordering,
    parse_bool,
    with_price_and_stock,
)
from catalog.models import Collection, Product
from catalog.utils.choices import ProductKind

CLIENT_PRODUCT_ORDERINGS = {
    "created_at": "created_at",
    "-created_at": "-created_at",
    "base_price": "base_price",
    "-base_price": "-base_price",
    "effective_price": "price_effective",
    "-effective_price": "-price_effective",
    "name": "name",
    "-name": "-name",
}


def kinds_from_param(value, default=(ProductKind.REGULAR,)):
    """`kind=REGULAR|CUSTOM|ALL` (comma separated allowed)."""
    values = [item.strip().upper() for item in (value or "").split(",") if item.strip()]
    if not values:
        return list(default)
    if "ALL" in values:
        return list(ProductKind.values)
    return [item for item in values if item in ProductKind.values] or list(default)


class ProductListAPIView(GenericAPIView):
    """Public product listing with search and catalog filters.

    Regular products only, unless `kind=CUSTOM` or `kind=ALL` is passed.
    """

    permission_classes = []
    authentication_classes = []
    serializer_class = ProductCardSerializer
    pagination_class = CustomPagination
    default_kinds = (ProductKind.REGULAR,)
    fixed_kinds = None

    def get_kinds(self):
        if self.fixed_kinds is not None:
            return list(self.fixed_kinds)
        return kinds_from_param(self.request.query_params.get("kind"), self.default_kinds)

    def get_queryset(self):
        queryset = with_price_and_stock(
            Product.objects.filter(status="ACTIVE", kind__in=self.get_kinds())
        ).prefetch_related("images", "collections")

        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(name_bn__icontains=search)
                | Q(description__icontains=search)
                | Q(fabric__icontains=search)
                | Q(design_code__iexact=search)
            )

        if parse_bool(params.get("featured")) is True:
            queryset = queryset.filter(is_featured=True)

        queryset = apply_common_filters(queryset, params)
        queryset = apply_ordering(
            queryset, params.get("ordering", "-created_at"), CLIENT_PRODUCT_ORDERINGS
        )
        return queryset.distinct()

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
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
            message=self.list_message,
        )

    list_message = "Products fetched successfully."


class ProductDetailAPIView(GenericAPIView):
    """Public single product view by slug or id."""

    permission_classes = []
    authentication_classes = []
    serializer_class = ProductDetailSerializer

    def get_queryset(self):
        return with_price_and_stock(
            Product.objects.filter(status="ACTIVE")
        ).prefetch_related("images", "collections", "variants")

    def get(self, request, slug_or_id, *args, **kwargs):
        product = self.get_queryset().filter(slug=slug_or_id).first()
        if product is None:
            try:
                product = self.get_queryset().get(pk=slug_or_id)
            except (ValueError, ValidationError, Product.DoesNotExist):
                product = None
        if product is None:
            return APIResponse.error(
                message="Product not found.",
                status=404,
            )
        serializer = self.get_serializer(product)
        return APIResponse.success(
            data=serializer.data,
            message="Product fetched successfully.",
        )


class CustomTemplateListAPIView(ProductListAPIView):
    """Public listing of custom order templates (same filters as products)."""

    fixed_kinds = (ProductKind.CUSTOM,)
    list_message = "Custom templates fetched successfully."


class CustomTemplateDetailAPIView(GenericAPIView):
    """Public custom template by slug, id or design code."""

    permission_classes = []
    authentication_classes = []
    serializer_class = ProductDetailSerializer

    def get_queryset(self):
        return with_price_and_stock(
            Product.objects.filter(status="ACTIVE", kind=ProductKind.CUSTOM)
        ).prefetch_related("images", "collections")

    def get(self, request, lookup, *args, **kwargs):
        queryset = self.get_queryset()
        template = queryset.filter(Q(slug=lookup) | Q(design_code__iexact=lookup)).first()
        if template is None:
            try:
                template = queryset.get(pk=lookup)
            except (ValueError, ValidationError, Product.DoesNotExist):
                template = None
        if template is None:
            return APIResponse.error(message="Custom template not found.", status=404)
        return APIResponse.success(
            data=self.get_serializer(template).data,
            message="Custom template fetched successfully.",
        )


ACTIVE_REGULAR = Q(products__status="ACTIVE", products__kind=ProductKind.REGULAR)
ACTIVE_CUSTOM = Q(products__status="ACTIVE", products__kind=ProductKind.CUSTOM)


class CollectionListAPIView(GenericAPIView):
    """Public collection listing."""

    permission_classes = []
    authentication_classes = []
    serializer_class = CollectionSerializer

    def get_queryset(self):
        queryset = Collection.objects.filter(is_active=True).annotate(
            product_count=Count("products", filter=ACTIVE_REGULAR, distinct=True),
            custom_template_count=Count("products", filter=ACTIVE_CUSTOM, distinct=True),
        )
        if parse_bool(self.request.query_params.get("has_custom_templates")) is True:
            queryset = queryset.filter(custom_template_count__gt=0)
        types = [
            value.strip().upper()
            for value in self.request.query_params.get("type", "").split(",")
            if value.strip()
        ]
        if types:
            queryset = queryset.filter(type__in=types)
        return queryset.order_by("position", "name")

    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return APIResponse.success(
            data=serializer.data,
            message="Collections fetched successfully.",
        )


class CollectionDetailAPIView(GenericAPIView):
    """Public single collection view with its active products and custom templates."""

    permission_classes = []
    authentication_classes = []
    serializer_class = CollectionSerializer

    def get_queryset(self):
        return Collection.objects.filter(is_active=True).annotate(
            product_count=Count("products", filter=ACTIVE_REGULAR, distinct=True),
            custom_template_count=Count("products", filter=ACTIVE_CUSTOM, distinct=True),
        )

    def get(self, request, slug, *args, **kwargs):
        collection = (
            self.get_queryset()
            .filter(Q(slug=slug) | Q(name__iexact=slug))
            .first()
        )
        if collection is None:
            return APIResponse.error(message="Collection not found.", status=404)

        data = self.get_serializer(collection).data
        products = list(
            with_price_and_stock(collection.products.filter(status="ACTIVE"))
            .prefetch_related("images", "collections")
            .order_by("-created_at")
        )
        data["products"] = ProductCardSerializer(
            [product for product in products if product.kind == ProductKind.REGULAR],
            many=True,
        ).data
        data["custom_templates"] = ProductCardSerializer(
            [product for product in products if product.kind == ProductKind.CUSTOM],
            many=True,
        ).data
        return APIResponse.success(
            data=data,
            message="Collection fetched successfully.",
        )
