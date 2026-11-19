from django.contrib import admin

from orders.models import (
    Order,
    OrderAttachment,
    OrderItem,
    OrderStatusEvent,
    Payment,
    Refund,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "product",
        "variant",
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
    can_delete = False


class OrderAttachmentInline(admin.TabularInline):
    model = OrderAttachment
    extra = 0
    readonly_fields = ("kind", "source", "url", "file_name", "note", "created_by", "created_at")
    fields = readonly_fields


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = ("status", "kind", "note", "created_by", "created_at")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "order_type",
        "customer_name",
        "customer_phone",
        "status",
        "payment_status",
        "payment_plan",
        "total",
        "paid_amount",
        "created_at",
    )
    list_filter = ("order_type", "status", "payment_status", "payment_plan")
    search_fields = (
        "order_number",
        "customer_name",
        "customer_email",
        "customer_phone",
    )
    readonly_fields = (
        "order_number",
        "subtotal",
        "delivery_fee",
        "total",
        "paid_amount",
        "refunded_amount",
        "stock_reserved",
        "placed_by",
        "created_at",
        "updated_at",
        "delivered_at",
    )
    inlines = (OrderItemInline, OrderAttachmentInline, OrderStatusEventInline)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "method",
        "plan",
        "amount",
        "status",
        "bkash_trx_id",
        "created_at",
    )
    list_filter = ("status", "method", "plan")
    search_fields = ("order__order_number", "bkash_payment_id", "bkash_trx_id")
    readonly_fields = ("created_at", "updated_at", "completed_at")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("order", "amount", "method", "trx_id", "created_by", "created_at")
    list_filter = ("method",)
    search_fields = ("order__order_number", "trx_id")
    readonly_fields = ("created_at", "updated_at")
