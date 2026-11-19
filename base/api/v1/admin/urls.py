from django.urls import path
from base.api.v1.admin import views

urlpatterns = [
    path("update/", views.StoreConfigUpdateAPIView.as_view(), name="store-config-update"),
]
