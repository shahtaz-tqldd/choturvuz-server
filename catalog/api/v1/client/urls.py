from django.urls import path

from catalog.api.v1.client import views

urlpatterns = [
    path("products/", views.ProductListAPIView.as_view(), name="client-product-list"),
    path(
        "products/<str:slug_or_id>/",
        views.ProductDetailAPIView.as_view(),
        name="client-product-detail",
    ),
    path(
        "custom-templates/",
        views.CustomTemplateListAPIView.as_view(),
        name="client-custom-template-list",
    ),
    path(
        "custom-templates/<str:lookup>/",
        views.CustomTemplateDetailAPIView.as_view(),
        name="client-custom-template-detail",
    ),
    path(
        "collections/",
        views.CollectionListAPIView.as_view(),
        name="client-collection-list",
    ),
    path(
        "collections/<str:slug>/",
        views.CollectionDetailAPIView.as_view(),
        name="client-collection-detail",
    ),
]
