from django.urls import path

from reviews.api.v1.admin import views

urlpatterns = [
    path("", views.AdminReviewListAPIView.as_view(), name="admin-review-list"),
    path("bulk/", views.AdminReviewBulkAPIView.as_view(), name="admin-review-bulk"),
    path("<uuid:pk>/", views.AdminReviewDetailAPIView.as_view(), name="admin-review-detail"),
]
