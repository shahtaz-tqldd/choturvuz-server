from decimal import Decimal

from rest_framework import serializers

from app.utils.phone import normalize_phone
from django.conf import settings

from orders.api.v1.serializers import (
    OrderAttachmentSerializer,
    OrderSerializer,
    OrderStatusEventSerializer,
    phone_regex,
)
from orders.models import Order, Payment, Refund
from orders.utils.choices import AttachmentKind, OrderStatus, PaymentPlan, RefundMethod


class StaffUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="pk", read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)


class AdminOrderEventSerializer(OrderStatusEventSerializer):
    created_by = StaffUserSerializer(read_only=True, allow_null=True)


class AdminOrderAttachmentSerializer(OrderAttachmentSerializer):
    created_by = StaffUserSerializer(read_only=True, allow_null=True)

    class Meta(OrderAttachmentSerializer.Meta):
        fields = OrderAttachmentSerializer.Meta.fields + ("public_id", "created_by")


class AdminRefundSerializer(serializers.ModelSerializer):
    created_by = StaffUserSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Refund
        fields = (
            "id",
            "amount",
            "method",
            "trx_id",
            "note",
            "payment",
            "created_by",
            "created_at",
        )


class AdminOrderListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    payment_status_display = serializers.CharField(
        source="get_payment_status_display", read_only=True
    )
    due_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    is_cod = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "order_type",
            "status",
            "status_display",
            "payment_status",
            "payment_status_display",
            "payment_plan",
            "is_cod",
            "customer_name",
            "customer_email",
            "customer_phone",
            "city",
            "total",
            "paid_amount",
            "due_amount",
            "refunded_amount",
            "item_count",
            "total_quantity",
            "needed_by",
            "quoted_at",
            "courier",
            "tracking_number",
            "delivered_at",
            "created_at",
        )

    def get_is_cod(self, order):
        return order.payment_plan == PaymentPlan.COD

    def get_item_count(self, order):
        return len(order.items.all())

    def get_total_quantity(self, order):
        return sum(item.quantity for item in order.items.all())


class AdminOrderSerializer(OrderSerializer):
    """Full order payload for the admin panel (every event, with staff info)."""

    placed_by_email = serializers.CharField(
        source="placed_by.email", read_only=True, default=None
    )
    refunds = AdminRefundSerializer(many=True, read_only=True)
    attachments = AdminOrderAttachmentSerializer(many=True, read_only=True)
    stock_reserved = serializers.BooleanField(read_only=True)

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + ("placed_by_email", "stock_reserved")

    def get_events(self, order):
        return AdminOrderEventSerializer(order.events.all(), many=True).data


class AdminOrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    courier = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tracking_number = serializers.CharField(
        max_length=80, required=False, allow_blank=True
    )


class AdminOrderBulkStatusSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=200
    )
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    courier = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tracking_number = serializers.CharField(
        max_length=80, required=False, allow_blank=True
    )


class AdminOrderDetailsUpdateSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=120, required=False)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    address_line = serializers.CharField(max_length=250, required=False)
    city = serializers.CharField(max_length=100, required=False)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    courier = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=80, required=False, allow_blank=True)
    recalculate_delivery_fee = serializers.BooleanField(required=False, default=True)

    def validate_customer_phone(self, value):
        if value:
            value = normalize_phone(value)
            phone_regex(value)
        return value

    def validate(self, attrs):
        fields = set(attrs) - {"recalculate_delivery_fee"}
        if not fields:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class AdminOrderNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000)


class AdminOrderRefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    method = serializers.ChoiceField(choices=RefundMethod.choices)
    trx_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    via_bkash = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Send the refund through the bKash refund API instead of only recording it.",
    )


class AdminOrderPaymentRecordSerializer(serializers.Serializer):
    """Record money collected outside bKash (e.g. the due half on delivery)."""

    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdminPaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    customer_name = serializers.CharField(
        source="order.customer_name", read_only=True
    )

    class Meta:
        model = Payment
        fields = (
            "id",
            "order",
            "order_number",
            "customer_name",
            "method",
            "plan",
            "amount",
            "status",
            "bkash_payment_id",
            "bkash_trx_id",
            "failure_reason",
            "completed_at",
            "created_at",
        )


class AdminCustomQuoteItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.00"), required=False
    )
    quantity = serializers.IntegerField(min_value=1, required=False)

    def validate_quantity(self, value):
        limit = settings.CUSTOM_ORDER_MAX_LINE_QUANTITY
        if value > limit:
            raise serializers.ValidationError(f"Ensure this value is at most {limit}.")
        return value


class AdminCustomOrderQuoteSerializer(serializers.Serializer):
    """Final price for a custom order. Omitted fields keep their current value."""

    items = AdminCustomQuoteItemSerializer(many=True, required=False)
    customization_fee = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.00"), required=False
    )
    discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.00"), required=False
    )
    delivery_fee = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        allow_null=True,
        help_text="Leave out (or null) to recalculate from the city.",
    )
    payment_plan = serializers.ChoiceField(choices=PaymentPlan.choices, required=False)
    note = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=True,
        default="",
        help_text="Shown to the customer on the tracking page.",
    )


class AdminOrderAttachmentCreateSerializer(serializers.Serializer):
    """Staff attach a file (e.g. a mockup): upload `file` or pass an existing `url`."""

    file = serializers.FileField(required=False)
    url = serializers.URLField(max_length=1024, required=False)
    kind = serializers.ChoiceField(choices=AttachmentKind.choices, default=AttachmentKind.MOCKUP)
    file_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_file(self, file):
        max_mb = settings.CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB
        if file.size > max_mb * 1024 * 1024:
            raise serializers.ValidationError(f"File cannot exceed {max_mb} MB.")
        return file

    def validate(self, attrs):
        if bool(attrs.get("file")) == bool(attrs.get("url")):
            raise serializers.ValidationError("Send exactly one of file or url.")
        return attrs
