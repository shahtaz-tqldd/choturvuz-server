from django.urls import path

from accounts.api.v1.client.views import RefreshTokenView
from accounts.api.v1.admin.views import (
    AccountListAPIView,
    AdminDetailsAPIView,
    AdminLoginAPIView,
    UpdateAdminInfoAPIView,
    UpdateAdminPasswordAPIView,
)

urlpatterns = [
    path("login/", AdminLoginAPIView.as_view(), name="admin-login"),
    # Same as /api/v1/accounts/refresh/, which also works for staff tokens.
    path("refresh/", RefreshTokenView.as_view(), name="admin-refresh-token"),
    path("update-info/", UpdateAdminInfoAPIView.as_view(), name="update-admin-info"),
    path(
        "update-password/",
        UpdateAdminPasswordAPIView.as_view(),
        name="update-admin-password",
    ),
    path("me/", AdminDetailsAPIView.as_view(), name="admin-details"),
    path("account-list/", AccountListAPIView.as_view(), name="account-list"),
]
