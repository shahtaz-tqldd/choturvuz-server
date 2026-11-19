from django.db.models import Q
from rest_framework import serializers

from app.utils.phone import normalize_phone
from catalog.models import Product
from orders.api.v1.serializers import phone_regex
from orders.models import Order
from orders.utils.choices import OrderStatus
from reviews.models import Review
from reviews.utils.choices import ReviewStatus

class ReviewProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "slug", "kind")

class PublicReviewSerializer(serializers.ModelSerializer):
    """What the site shows. Contact details are never included."""

    product = ReviewProductSerializer(read_only=True, allow_null=True)
    is_verified_purchase = serializers.BooleanField(read_only=True)

    class Meta:
        model = Review
        fields = (
            "id",
            "customer_name",
            "customer_title",
            "rating",
            "title",
            "comment",
            "is_featured",
            "is_verified_purchase",
            "product",
            "published_at",
            "created_at",
        )

def resolve_product(value):
    if not value:
        return None
    product = Product.objects.filter(pk=value, status="ACTIVE").first()
    if product is None:
        raise serializers.ValidationError({"product_id": ["This product is not available."]})
    return product

class ReviewCreateSerializer(serializers.Serializer):
    """Customer submits a review. It stays PENDING until staff approve it."""

    customer_name = serializers.CharField(max_length=120)
    customer_title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    comment = serializers.CharField(min_length=5, max_length=2000)
    product_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    order_number = serializers.CharField(
        max_length=24,
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional: marks the review as a verified purchase.",
    )

    def validate_customer_phone(self, value):
        if value:
            value = normalize_phone(value)
            phone_regex(value)
        return value

    def validate(self, attrs):
        attrs["customer_email"] = attrs["customer_email"].strip().lower()
        attrs["product"] = resolve_product(attrs.pop("product_id"))
        attrs["order"] = self._verified_order(attrs)
        return attrs

    def _verified_order(self, attrs):
        order_number = attrs.pop("order_number").strip().upper()
        if not order_number:
            return None
        email, phone = attrs["customer_email"], attrs["customer_phone"]
        if not email and not phone:
            raise serializers.ValidationError(
                {"order_number": ["Add the email or phone used for this order."]}
            )
        contact = Q()
        if email:
            contact |= Q(customer_email=email)
        if phone:
            contact |= Q(customer_phone=phone)
        order = (
            Order.objects.filter(order_number=order_number)
            .exclude(status=OrderStatus.CANCELLED)
            .filter(contact)
            .first()
        )
        if order is None:
            raise serializers.ValidationError(
                {"order_number": ["No order found for that number and email/phone."]}
            )
        if Review.objects.filter(order=order).exists():
            raise serializers.ValidationError(
                {"order_number": ["This order has already been reviewed."]}
            )
        return order

class StaffUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="pk", read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)

class AdminReviewSerializer(serializers.ModelSerializer):
    """Full review for staff; also used to create and edit reviews."""

    product = ReviewProductSerializer(read_only=True, allow_null=True)
    product_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True, default=None)
    is_verified_purchase = serializers.BooleanField(read_only=True)
    moderated_by = StaffUserSerializer(read_only=True, allow_null=True)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    status = serializers.ChoiceField(choices=ReviewStatus.choices, required=False)

    class Meta:
        model = Review
        fields = (
            "id",
            "customer_name",
            "customer_title",
            "customer_email",
            "customer_phone",
            "rating",
            "title",
            "comment",
            "status",
            "source",
            "is_featured",
            "is_verified_purchase",
            "product",
            "product_id",
            "order_number",
            "moderated_by",
            "moderated_at",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "source",
            "moderated_at",
            "published_at",
            "created_at",
            "updated_at",
        )

    def validate_customer_phone(self, value):
        if value:
            value = normalize_phone(value)
            phone_regex(value)
        return value

    def validate(self, attrs):
        if "product_id" in attrs:
            product_id = attrs.pop("product_id")
            attrs["product"] = (
                Product.objects.filter(pk=product_id).first() if product_id else None
            )
            if product_id and attrs["product"] is None:
                raise serializers.ValidationError({"product_id": ["Unknown product."]})
        return attrs

class AdminReviewBulkSerializer(serializers.Serializer):
    ACTIONS = ("approve", "hide", "pending", "feature", "unfeature", "delete")

    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=500)
    action = serializers.ChoiceField(choices=ACTIONS)
