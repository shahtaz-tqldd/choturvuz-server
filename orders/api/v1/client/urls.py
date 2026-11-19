from django.urls import path

from orders.api.v1.client import views

urlpatterns = [
    path("", views.OrderCreateAPIView.as_view(), name="client-order-create"),
    path("quote/", views.OrderQuoteAPIView.as_view(), name="client-order-quote"),
    path("track/", views.OrderTrackAPIView.as_view(), name="client-order-track"),
    path(
        "custom/",
        views.CustomOrderCreateAPIView.as_view(),
        name="client-custom-order-create",
    ),
    path(
        "custom/estimate/",
        views.CustomOrderEstimateAPIView.as_view(),
        name="client-custom-order-estimate",
    ),
    path(
        "custom/uploads/",
        views.CustomOrderUploadAPIView.as_view(),
        name="client-custom-order-upload",
    ),
    path(
        "custom/<str:order_number>/attachments/",
        views.CustomOrderAttachmentsAPIView.as_view(),
        name="client-custom-order-attachments",
    ),
    path(
        "<str:order_number>/pay/",
        views.OrderPayAPIView.as_view(),
        name="client-order-pay",
    ),
]
