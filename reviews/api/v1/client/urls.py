from django.urls import path

from reviews.api.v1.client import views

urlpatterns = [
    path("", views.ReviewListCreateAPIView.as_view(), name="client-review-list"),
    path("summary/", views.ReviewSummaryAPIView.as_view(), name="client-review-summary"),
]
