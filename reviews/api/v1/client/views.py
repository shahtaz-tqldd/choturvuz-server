from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from reviews.api.v1.queries import REVIEW_ORDERINGS, filter_reviews
from reviews.api.v1.serializers import PublicReviewSerializer, ReviewCreateSerializer
from reviews.api.v1.throttles import ReviewCreateIPThrottle
from reviews.models import Review
from reviews.utils.choices import ReviewSource, ReviewStatus


def published_reviews():
    return Review.objects.filter(status=ReviewStatus.APPROVED).select_related("product")


def _meta(paginator, request):
    return {
        "count": paginator.page.paginator.count,
        "page": paginator.page.number,
        "page_size": paginator.get_page_size(request),
        "num_pages": paginator.page.paginator.num_pages,
        "next": paginator.get_next_link(),
        "previous": paginator.get_previous_link(),
    }


class ReviewListCreateAPIView(GenericAPIView):
    """GET: approved reviews for the site. POST: a customer submits a review."""

    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = CustomPagination
    throttle_classes = [ReviewCreateIPThrottle]

    def get_serializer_class(self):
        return ReviewCreateSerializer if self.request.method == "POST" else PublicReviewSerializer

    def get(self, request, *args, **kwargs):
        params = request.query_params
        queryset = filter_reviews(published_reviews(), params)
        ordering = REVIEW_ORDERINGS.get(params.get("ordering", ""), REVIEW_ORDERINGS["newest"])
        queryset = queryset.order_by(*ordering, "pk")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return APIResponse.success(
            data=PublicReviewSerializer(page, many=True).data,
            meta=_meta(paginator, request),
            message="Reviews fetched successfully.",
        )

    def post(self, request, *args, **kwargs):
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = Review.objects.create(
            source=ReviewSource.CUSTOMER,
            status=ReviewStatus.PENDING,
            **serializer.validated_data,
        )
        return APIResponse.success(
            data={
                "id": str(review.pk),
                "status": review.status,
                "is_verified_purchase": review.is_verified_purchase,
            },
            message="Thank you! Your review will appear on the site once it is approved.",
            status=status.HTTP_201_CREATED,
        )


class ReviewSummaryAPIView(GenericAPIView):
    """Average rating and star distribution of approved reviews."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        queryset = filter_reviews(published_reviews(), request.query_params)
        totals = queryset.aggregate(count=Count("id"), average=Avg("rating"))
        distribution = {str(star): 0 for star in range(5, 0, -1)}
        for row in queryset.values("rating").annotate(count=Count("id")):
            distribution[str(row["rating"])] = row["count"]
        average = totals["average"]
        return APIResponse.success(
            data={
                "count": totals["count"],
                "average_rating": round(float(average), 1) if average is not None else None,
                "distribution": distribution,
            },
            message="Review summary fetched successfully.",
        )
