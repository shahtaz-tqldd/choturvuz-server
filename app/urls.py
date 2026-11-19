from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings

from orders.api.v1.client.views import BkashCallbackAPIView

from orders.api.v1.admin.views import AdminPaymentListAPIView, AdminStatsAPIView

v1_client_urls = [
    path("base/", include("base.api.v1.client.urls")),
    path("accounts/", include("accounts.api.v1.client.urls")),
    path("catalog/", include("catalog.api.v1.client.urls")),
    path("orders/", include("orders.api.v1.client.urls")),
    path("reviews/", include("reviews.api.v1.client.urls")),
]

v1_admin_urls = [
    path("accounts/", include("accounts.api.v1.admin.urls")),
    path("base/", include("base.api.v1.admin.urls")),
    path("catalog/", include("catalog.api.v1.admin.urls")),
    path("orders/", include("orders.api.v1.admin.urls")),
    path("reviews/", include("reviews.api.v1.admin.urls")),
    path(
        "payments/",
        AdminPaymentListAPIView.as_view(),
        name="admin-payment-list",
    ),
    path("stats/", AdminStatsAPIView.as_view(), name="admin-stats"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(v1_client_urls)),
    path("api/v1/admin/", include(v1_admin_urls)),
    path(
        "api/v1/payments/bkash/callback/",
        BkashCallbackAPIView.as_view(),
        name="bkash-callback",
    ),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
