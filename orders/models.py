from decimal import Decimal

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from orders.utils.choices import (
    AttachmentKind,
    AttachmentSource,
    OrderEventKind,
    OrderType,
    OrderPaymentStatus,
    OrderStatus,
    PaymentMethod,
    PaymentPlan,
    PaymentStatus,
    RefundMethod,
)


def _zero():
    return Decimal("0.00")


class Order(BaseModel):
    order_number = models.CharField(max_length=24, unique=True, editable=False)
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        default=OrderType.REGULAR,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_PAYMENT,
        db_index=True,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=OrderPaymentStatus.choices,
        default=OrderPaymentStatus.UNPAID,
        db_index=True,
    )
    payment_plan = models.CharField(
        max_length=10,
        choices=PaymentPlan.choices,
        default=PaymentPlan.FULL,
    )

    # Guest-friendly customer identity; email or phone is enough to order/track.
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField(blank=True, db_index=True)
    customer_phone = models.CharField(max_length=20, db_index=True)

    # Shipping address
    address_line = models.CharField(max_length=250)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    # Money snapshot (in store currency)
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=_zero
    )
    delivery_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=_zero
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=_zero)
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=_zero
    )
    refunded_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=_zero
    )
    # Custom orders: set by staff in the quote (printing/setup charges and
    # any discount). Always zero for regular orders.
    customization_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=_zero
    )
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=_zero)

    # Custom order brief from the customer.
    custom_instructions = models.TextField(blank=True)
    size_guidelines = models.TextField(blank=True)
    needed_by = models.DateField(null=True, blank=True)
    # Final price sent by staff; the customer can pay once this is set.
    quoted_at = models.DateTimeField(null=True, blank=True)
    quote_note = models.TextField(blank=True)

    # True while the ordered quantities are deducted from variant stock.
    # Set when the order is placed; cleared when it is cancelled.
    stock_reserved = models.BooleanField(default=False)

    # Shipping
    courier = models.CharField(max_length=60, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)

    # Optional link when a logged-in user places the order.
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )

    cancelled_reason = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer_email", "-created_at"]),
            models.Index(fields=["customer_phone", "-created_at"]),
        ]

    def __str__(self):
        return self.order_number

    @property
    def is_custom(self):
        return self.order_type == OrderType.CUSTOM

    def compute_total(self):
        total = self.subtotal + self.customization_fee + self.delivery_fee - self.discount
        return max(total, Decimal("0.00"))

    @property
    def due_amount(self):
        return max(self.total - self.paid_amount, Decimal("0.00"))

    @property
    def advance_amount(self):
        """Amount payable upfront: half for HALF, nothing for COD, all for FULL."""
        if self.payment_plan == PaymentPlan.COD:
            return Decimal("0.00")
        if self.payment_plan == PaymentPlan.HALF:
            return (self.total / Decimal("2")).quantize(Decimal("0.01"))
        return self.total

    @property
    def refund_due(self):
        if self.payment_status != OrderPaymentStatus.REFUND_PENDING:
            return Decimal("0.00")
        return max(self.paid_amount - self.refunded_amount, Decimal("0.00"))


class OrderItem(BaseModel):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="order_items"
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    # Snapshots keep history intact even if the catalog changes later.
    product_name = models.CharField(max_length=200)
    variant_name = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # Custom order lines carry the requested size/colour instead of a variant.
    size = models.CharField(max_length=12, blank=True)
    color_name = models.CharField(max_length=60, blank=True)
    color_hex = models.CharField(max_length=7, blank=True)
    note = models.CharField(max_length=500, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"{self.order.order_number} — {self.product_name}"


class OrderStatusEvent(BaseModel):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="events"
    )
    status = models.CharField(max_length=20, choices=OrderStatus.choices)
    kind = models.CharField(
        max_length=10, choices=OrderEventKind.choices, default=OrderEventKind.STATUS
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_status_events",
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.order.order_number} -> {self.status}"


class Payment(BaseModel):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="payments"
    )
    method = models.CharField(
        max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.BKASH
    )
    plan = models.CharField(
        max_length=10, choices=PaymentPlan.choices, default=PaymentPlan.FULL
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED
    )
    bkash_payment_id = models.CharField(max_length=64, blank=True, db_index=True)
    bkash_trx_id = models.CharField(max_length=64, blank=True)
    failure_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_number} — {self.method} {self.amount} ({self.status})"


class Refund(BaseModel):
    """Money returned to a customer for a cancelled (or adjusted) order."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="refunds"
    )
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=RefundMethod.choices)
    trx_id = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_refunds",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_number} refund {self.amount} ({self.method})"


class OrderAttachment(BaseModel):
    """A design, logo or reference file attached to a (custom) order."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="attachments"
    )
    kind = models.CharField(
        max_length=12, choices=AttachmentKind.choices, default=AttachmentKind.DESIGN
    )
    source = models.CharField(
        max_length=10, choices=AttachmentSource.choices, default=AttachmentSource.CUSTOMER
    )
    url = models.URLField(max_length=1024)
    public_id = models.CharField(max_length=512)
    resource_type = models.CharField(max_length=10, default="image")
    file_name = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_attachments",
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.order.order_number} {self.kind} {self.file_name or self.public_id}"
