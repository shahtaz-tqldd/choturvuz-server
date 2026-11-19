from pathlib import Path

from django.conf import settings
from django.core.validators import RegexValidator
from django.utils import timezone
from rest_framework import serializers

from app.utils.phone import normalize_phone
from catalog.models import ProductVariant
from catalog.utils.choices import ProductKind, ProductSize
from orders.models import (
    Order,
    OrderAttachment,
    OrderItem,
    OrderStatusEvent,
    Payment,
    Refund,
)
from orders.services.orders import get_payment_due
from orders.utils.choices import (
    PUBLIC_EVENT_KINDS,
    AttachmentKind,
    OrderStatus,
    PaymentPlan,
)

phone_regex = RegexValidator(
    regex=r"^\+?\d{6,15}$",
    message="Enter a valid phone number (6-15 digits, optional leading '+').",
)


class OrderItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="product.pk", read_only=True)
    variant_id = serializers.UUIDField(source="variant.pk", read_only=True, default=None)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_id",
            "variant_id",
            "product_name",
            "variant_name",
            "sku",
            "unit_price",
            "quantity",
            "line_total",
            "size",
            "color_name",
            "color_hex",
            "note",
        )


class OrderAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderAttachment
        fields = (
            "id",
            "kind",
            "source",
            "url",
            "resource_type",
            "file_name",
            "note",
            "created_at",
        )


class OrderStatusEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusEvent
        fields = ("id", "status", "kind", "note", "created_by", "created_at")


class RefundReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ("id", "amount", "method", "trx_id", "note", "created_at")


class PaymentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "method",
            "plan",
            "amount",
            "status",
            "bkash_trx_id",
            "failure_reason",
            "completed_at",
            "created_at",
        )


class OrderSerializer(serializers.ModelSerializer):
    """Order as the customer sees it (also returned by /orders/track/).

    Internal notes and edit events are left out of `events`.
    """

    items = OrderItemSerializer(many=True, read_only=True)
    attachments = OrderAttachmentSerializer(many=True, read_only=True)
    events = serializers.SerializerMethodField()
    payments = PaymentReadSerializer(many=True, read_only=True)
    refunds = RefundReadSerializer(many=True, read_only=True)
    due_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    advance_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    payment_status_display = serializers.CharField(
        source="get_payment_status_display", read_only=True
    )
    payment_plan_display = serializers.CharField(
        source="get_payment_plan_display", read_only=True
    )
    is_cod = serializers.SerializerMethodField()
    is_custom = serializers.BooleanField(read_only=True)
    is_awaiting_quote = serializers.SerializerMethodField()
    requires_online_payment = serializers.SerializerMethodField()
    refund_due = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "order_type",
            "is_custom",
            "is_awaiting_quote",
            "status",
            "status_display",
            "payment_status",
            "payment_status_display",
            "payment_plan",
            "payment_plan_display",
            "is_cod",
            "requires_online_payment",
            "customer_name",
            "customer_email",
            "customer_phone",
            "address_line",
            "city",
            "postal_code",
            "notes",
            "custom_instructions",
            "size_guidelines",
            "needed_by",
            "quoted_at",
            "quote_note",
            "subtotal",
            "customization_fee",
            "discount",
            "delivery_fee",
            "total",
            "paid_amount",
            "due_amount",
            "advance_amount",
            "refunded_amount",
            "refund_due",
            "courier",
            "tracking_number",
            "shipped_at",
            "items",
            "attachments",
            "events",
            "payments",
            "refunds",
            "delivered_at",
            "cancelled_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_cod(self, order):
        return order.payment_plan == PaymentPlan.COD

    def get_is_awaiting_quote(self, order):
        return order.status == OrderStatus.PENDING_REVIEW

    def get_requires_online_payment(self, order):
        """True while the storefront should offer "Pay with bKash"."""
        return (
            order.payment_plan != PaymentPlan.COD
            and order.status == OrderStatus.PENDING_PAYMENT
            and get_payment_due(order) > 0
        )

    def get_events(self, order):
        events = [event for event in order.events.all() if event.kind in PUBLIC_EVENT_KINDS]
        return OrderStatusEventSerializer(events, many=True).data


class OrderCreateItemSerializer(serializers.Serializer):
    variant_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=20)

    def validate_variant_id(self, value):
        if not ProductVariant.objects.filter(
            pk=value,
            is_active=True,
            product__status="ACTIVE",
            product__kind=ProductKind.REGULAR,
        ).exists():
            raise serializers.ValidationError("This product variant is not available.")
        return value


class CustomerDetailsSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=120)
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    address_line = serializers.CharField(max_length=250)
    city = serializers.CharField(max_length=100)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    payment_plan = serializers.ChoiceField(
        choices=PaymentPlan.choices, default=PaymentPlan.FULL
    )

    def validate_customer_phone(self, value):
        if value:
            value = normalize_phone(value)
            phone_regex(value)
        return value

    def validate(self, attrs):
        if not attrs.get("customer_email") and not attrs.get("customer_phone"):
            raise serializers.ValidationError(
                "Provide at least one of customer_email or customer_phone."
            )
        return attrs


class OrderCreateSerializer(CustomerDetailsSerializer):
    items = OrderCreateItemSerializer(many=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)

        items = attrs.get("items", [])
        if not items:
            raise serializers.ValidationError({"items": "Add at least one item."})

        merged = {}
        for item in items:
            key = str(item["variant_id"])
            merged[key] = merged.get(key, 0) + item["quantity"]
        if len(merged) != len(items):
            raise serializers.ValidationError(
                {"items": "Combine duplicate variants into a single line."}
            )
        attrs["merged_items"] = [
            {"variant_id": key, "quantity": qty} for key, qty in merged.items()
        ]
        return attrs


class OrderQuoteSerializer(serializers.Serializer):
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    payment_plan = serializers.ChoiceField(
        choices=PaymentPlan.choices, default=PaymentPlan.FULL
    )
    items = serializers.ListField(
        child=serializers.DictField(), allow_empty=False, max_length=50
    )

    def validate_items(self, items):
        """Validate shape only; unknown or inactive variants are reported per line."""
        merged = {}
        errors = []
        for index, item in enumerate(items):
            line = OrderQuoteItemSerializer(data=item)
            if not line.is_valid():
                errors.append({str(index): line.errors})
                continue
            key = str(line.validated_data["variant_id"])
            merged[key] = merged.get(key, 0) + line.validated_data["quantity"]
        if errors:
            raise serializers.ValidationError(errors)
        return [{"variant_id": key, "quantity": qty} for key, qty in merged.items()]


class OrderQuoteItemSerializer(serializers.Serializer):
    variant_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=20)


class OrderQuoteLineSerializer(serializers.Serializer):
    variant_id = serializers.CharField()
    product_id = serializers.CharField(required=False, default=None)
    product_name = serializers.CharField(required=False, default=None)
    product_slug = serializers.CharField(required=False, default=None)
    variant_name = serializers.CharField(required=False, default=None)
    sku = serializers.CharField(required=False, default=None)
    quantity = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    stock = serializers.IntegerField()
    available = serializers.BooleanField()
    reason = serializers.CharField(allow_null=True)


class OrderQuoteResultSerializer(serializers.Serializer):
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = serializers.DecimalField(max_digits=12, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_plan = serializers.CharField()
    free_delivery_threshold = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True
    )
    all_available = serializers.BooleanField()
    items = OrderQuoteLineSerializer(many=True)


class OrderTrackSerializer(serializers.Serializer):
    order_number = serializers.CharField(max_length=24)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError(
                "Provide the email or phone used when placing the order."
            )
        return attrs


class OrderPaySerializer(serializers.Serializer):
    pass


# ---------------------------------------------------------------------------
# Custom orders
# ---------------------------------------------------------------------------


class CustomOrderLineSerializer(serializers.Serializer):
    size = serializers.ChoiceField(
        choices=ProductSize.choices, required=False, allow_blank=True, default=""
    )
    color_name = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )
    color_hex = serializers.RegexField(
        r"^#[0-9A-Fa-f]{6}$", required=False, allow_blank=True, default=""
    )
    quantity = serializers.IntegerField(min_value=1)
    note = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default="",
        help_text="Per-line personalisation, e.g. names and jersey numbers.",
    )

    def validate_quantity(self, value):
        limit = settings.CUSTOM_ORDER_MAX_LINE_QUANTITY
        if value > limit:
            raise serializers.ValidationError(f"Ensure this value is at most {limit}.")
        return value


class CustomOrderAttachmentInputSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=1024)
    kind = serializers.ChoiceField(
        choices=[
            choice for choice in AttachmentKind.choices if choice[0] != AttachmentKind.MOCKUP
        ],
        default=AttachmentKind.DESIGN,
    )
    file_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class CustomOrderEstimateSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    payment_plan = serializers.ChoiceField(
        choices=PaymentPlan.choices, default=PaymentPlan.FULL
    )
    items = CustomOrderLineSerializer(many=True, allow_empty=False)


class CustomOrderCreateSerializer(CustomerDetailsSerializer):
    template_id = serializers.UUIDField()
    items = CustomOrderLineSerializer(many=True, allow_empty=False)
    attachments = CustomOrderAttachmentInputSerializer(many=True, required=False, default=list)
    custom_instructions = serializers.CharField(
        max_length=5000, required=False, allow_blank=True, default=""
    )
    size_guidelines = serializers.CharField(
        max_length=5000, required=False, allow_blank=True, default=""
    )
    needed_by = serializers.DateField(required=False, allow_null=True, default=None)

    def validate_needed_by(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError("Choose a date in the future.")
        return value

    def validate_attachments(self, value):
        limit = settings.CUSTOM_ORDER_MAX_ATTACHMENTS
        if len(value) > limit:
            raise serializers.ValidationError(f"Attach at most {limit} files.")
        return value


class CustomOrderUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, file):
        extension = Path(file.name or "").suffix.lower().lstrip(".")
        allowed = settings.CUSTOM_ORDER_UPLOAD_EXTENSIONS
        if extension not in allowed:
            raise serializers.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(allowed)}."
            )
        max_mb = settings.CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB
        if file.size > max_mb * 1024 * 1024:
            raise serializers.ValidationError(f"File cannot exceed {max_mb} MB.")
        return file


class CustomOrderAddAttachmentsSerializer(serializers.Serializer):
    """Customer adds files to their custom order (proves ownership like /track/)."""

    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    attachments = CustomOrderAttachmentInputSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError(
                "Provide the email or phone used when placing the order."
            )
        return attrs


class CustomOrderEstimateLineSerializer(serializers.Serializer):
    size = serializers.CharField()
    color_name = serializers.CharField()
    color_hex = serializers.CharField()
    variant_name = serializers.CharField()
    quantity = serializers.IntegerField()
    note = serializers.CharField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2)


class CustomOrderEstimateResultSerializer(serializers.Serializer):
    template_id = serializers.CharField()
    design_code = serializers.CharField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_quantity = serializers.IntegerField()
    min_order_quantity = serializers.IntegerField()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = serializers.DecimalField(max_digits=12, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_plan = serializers.CharField()
    is_estimate = serializers.BooleanField()
    items = CustomOrderEstimateLineSerializer(many=True)
