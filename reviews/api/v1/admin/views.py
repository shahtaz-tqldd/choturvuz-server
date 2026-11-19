from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.services.revalidate import REVIEWS, queue_revalidation
from app.utils.pagination import CustomPagination
from app.utils.permission import IsAdmin
from app.utils.response import APIResponse
from reviews.api.v1.client.views import _meta
from reviews.api.v1.queries import REVIEW_ORDERINGS, filter_reviews
from reviews.api.v1.serializers import AdminReviewBulkSerializer, AdminReviewSerializer
from reviews.models import Review
from reviews.services import apply_status
from reviews.utils.choices import ReviewSource, ReviewStatus

ADMIN_ORDERINGS = {
    **REVIEW_ORDERINGS,
    "created_at": ("created_at",),
    "-created_at": ("-created_at",),
}


def _queryset():
    return Review.objects.select_related("product", "order", "moderated_by")


class AdminReviewListAPIView(GenericAPIView):
    """List every review (any status) or add one as staff."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminReviewSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        params = self.request.query_params
        queryset = filter_reviews(_queryset(), params)

        for param, field in (("status", "status"), ("source", "source")):
            values = [v.strip().upper() for v in params.get(param, "").split(",") if v.strip()]
            if values:
                queryset = queryset.filter(**{f"{field}__in": values})

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(customer_phone__icontains=search)
                | Q(title__icontains=search)
                | Q(comment__icontains=search)
                | Q(order__order_number__iexact=search)
            )
        ordering = ADMIN_ORDERINGS.get(params.get("ordering", ""), ("-created_at",))
        return queryset.order_by(*ordering, "pk")

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(self.get_queryset(), request, view=self)
        status_counts = {value: 0 for value in ReviewStatus.values}
        for row in Review.objects.values("status").annotate(count=Count("id")):
            status_counts[row["status"]] = row["count"]
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={**_meta(paginator, request), "status_counts": status_counts},
            message="Reviews fetched successfully.",
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        target_status = data.pop("status", ReviewStatus.APPROVED)
        review = Review(source=ReviewSource.STAFF, **data)
        apply_status(review, target_status, user=request.user)
        review.save()
        return APIResponse.success(
            data=self.get_serializer(_queryset().get(pk=review.pk)).data,
            message="Review created successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminReviewDetailAPIView(GenericAPIView):
    """Retrieve, edit (including approve/hide/feature) or delete a review."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminReviewSerializer

    def _get(self, pk):
        return _queryset().filter(pk=pk).first()

    def _not_found(self):
        return APIResponse.error(message="Review not found.", status=404)

    def get(self, request, pk, *args, **kwargs):
        review = self._get(pk)
        if review is None:
            return self._not_found()
        return APIResponse.success(
            data=self.get_serializer(review).data, message="Review fetched successfully."
        )

    def patch(self, request, pk, *args, **kwargs):
        review = self._get(pk)
        if review is None:
            return self._not_found()
        serializer = self.get_serializer(review, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        new_status = data.pop("status", None)
        for field, value in data.items():
            setattr(review, field, value)
        if new_status is not None:
            apply_status(review, new_status, user=request.user)
        review.save()
        return APIResponse.success(
            data=self.get_serializer(self._get(pk)).data,
            message="Review updated successfully.",
        )

    def delete(self, request, pk, *args, **kwargs):
        review = self._get(pk)
        if review is None:
            return self._not_found()
        review.delete()
        return APIResponse.success(message="Review deleted successfully.")


class AdminReviewBulkAPIView(GenericAPIView):
    """Approve, hide, feature, unfeature or delete many reviews at once."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminReviewBulkSerializer

    STATUS_ACTIONS = {
        "approve": ReviewStatus.APPROVED,
        "hide": ReviewStatus.HIDDEN,
        "pending": ReviewStatus.PENDING,
    }

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        reviews = list(Review.objects.filter(pk__in=serializer.validated_data["ids"]))

        with transaction.atomic():
            if action == "delete":
                Review.objects.filter(pk__in=[review.pk for review in reviews]).delete()
            elif action in ("feature", "unfeature"):
                Review.objects.filter(pk__in=[review.pk for review in reviews]).update(
                    is_featured=action == "feature"
                )
            else:
                for review in reviews:
                    fields = apply_status(review, self.STATUS_ACTIONS[action], user=request.user)
                    if fields:
                        review.save(update_fields=fields + ["updated_at"])
            # Queryset .update()/.delete() skip per-row signals.
            queue_revalidation(REVIEWS)

        return APIResponse.success(
            data={"action": action, "affected": len(reviews)},
            message=f"{len(reviews)} review(s) updated.",
        )
