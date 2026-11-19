from django.urls import path

from catalog.api.v1.admin import views

urlpatterns = [
    path("products/", views.AdminProductListAPIView.as_view(), name="admin-product-list"),
    path(
        "products/bulk/",
        views.AdminProductBulkActionAPIView.as_view(),
        name="admin-product-bulk",
    ),
    path(
        "variants/bulk-stock/",
        views.AdminVariantBulkStockAPIView.as_view(),
        name="admin-variant-bulk-stock",
    ),
    path(
        "variants/<uuid:pk>/",
        views.AdminVariantDetailAPIView.as_view(),
        name="admin-variant-detail",
    ),
    path(
        "variants/<uuid:pk>/adjust/",
        views.AdminVariantAdjustStockAPIView.as_view(),
        name="admin-variant-adjust",
    ),
    path(
        "stock-movements/",
        views.AdminStockMovementListAPIView.as_view(),
        name="admin-stock-movements",
    ),
    path(
        "products/<uuid:pk>/",
        views.AdminProductDetailAPIView.as_view(),
        name="admin-product-detail",
    ),
    path(
        "collections/",
        views.AdminCollectionListAPIView.as_view(),
        name="admin-collection-list",
    ),
    path(
        "collections/<uuid:pk>/",
        views.AdminCollectionDetailAPIView.as_view(),
        name="admin-collection-detail",
    ),
    path(
        "collections/<uuid:pk>/products/",
        views.AdminCollectionProductsAPIView.as_view(),
        name="admin-collection-products",
    ),
]
