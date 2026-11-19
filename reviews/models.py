from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from app.core.models import BaseModel
from reviews.utils.choices import ReviewSource, ReviewStatus


class Review(BaseModel):
    """A store-wide customer review (testimonial).

    Reviews are about the shop overall. `product` is an optional reference
    kept for future per-product reviews; `order` marks a verified purchase.
    """

    customer_name = models.CharField(max_length=120)
    # Shown under the name, e.g. "CSE '27, University of Dhaka" or "Sylhet tour group".
    customer_title = models.CharField(max_length=120, blank=True)
    # Private: used by staff to follow up, never returned by public APIs.
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)

    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=150, blank=True)
    comment = models.TextField(max_length=2000)

    status = models.CharField(
        max_length=10,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    source = models.CharField(
        max_length=10, choices=ReviewSource.choices, default=ReviewSource.CUSTOMER
    )
    is_featured = models.BooleanField(default=False, db_index=True)

    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviews",
    )
    order = models.OneToOneField(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="review",
    )

    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderated_reviews",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)
    # When it first went live; drives "newest" ordering on the site.
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-published_at"])]

    def __str__(self):
        return f"{self.customer_name} ({self.rating}★)"

    @property
    def is_verified_purchase(self):
        return self.order_id is not None
