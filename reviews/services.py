from django.utils import timezone

from reviews.utils.choices import ReviewStatus


def apply_status(review, status, *, user=None):
    """Set a review's moderation status and its audit fields (not saved).

    Returns the list of changed field names.
    """
    if status == review.status and review.moderated_at is not None:
        return []
    review.status = status
    review.moderated_by = user if getattr(user, "is_authenticated", False) else None
    review.moderated_at = timezone.now()
    fields = ["status", "moderated_by", "moderated_at"]
    if status == ReviewStatus.APPROVED and review.published_at is None:
        review.published_at = review.moderated_at
        fields.append("published_at")
    return fields
