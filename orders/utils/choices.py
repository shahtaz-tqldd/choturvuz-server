from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderType(models.TextChoices):
    REGULAR = "REGULAR", _("Regular")
    CUSTOM = "CUSTOM", _("Custom")


class OrderStatus(models.TextChoices):
    # Custom orders start here: staff review the artwork and send a quote.
    PENDING_REVIEW = "PENDING_REVIEW", _("Pending Review")
    PENDING_PAYMENT = "PENDING_PAYMENT", _("Pending Payment")
    # Cash-on-delivery orders wait here until staff confirm them by phone.
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION", _("Pending Confirmation")
    CONFIRMED = "CONFIRMED", _("Confirmed")
    PROCESSING = "PROCESSING", _("Processing")
    SHIPPED = "SHIPPED", _("Shipped")
    DELIVERED = "DELIVERED", _("Delivered")
    CANCELLED = "CANCELLED", _("Cancelled")


class OrderPaymentStatus(models.TextChoices):
    UNPAID = "UNPAID", _("Unpaid")
    PARTIALLY_PAID = "PARTIALLY_PAID", _("Partially Paid")
    PAID = "PAID", _("Paid")
    # Cancelled after money was collected; a refund still has to be made.
    REFUND_PENDING = "REFUND_PENDING", _("Refund Pending")
    REFUNDED = "REFUNDED", _("Refunded")


class PaymentPlan(models.TextChoices):
    FULL = "FULL", _("Full Payment")
    HALF = "HALF", _("Half Advance")
    COD = "COD", _("Cash on Delivery")


BKASH_PAYMENT_PLANS = (PaymentPlan.FULL, PaymentPlan.HALF)


class PaymentMethod(models.TextChoices):
    BKASH = "BKASH", _("bKash")
    COD = "COD", _("Cash on Delivery")


class PaymentStatus(models.TextChoices):
    INITIATED = "INITIATED", _("Initiated")
    PENDING = "PENDING", _("Pending")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    CANCELLED = "CANCELLED", _("Cancelled")


class OrderEventKind(models.TextChoices):
    STATUS = "STATUS", _("Status change")
    NOTE = "NOTE", _("Internal note")
    PAYMENT = "PAYMENT", _("Payment")
    REFUND = "REFUND", _("Refund")
    EDIT = "EDIT", _("Order edited")


# Event kinds customers may see on the public tracking page.
PUBLIC_EVENT_KINDS = (
    OrderEventKind.STATUS,
    OrderEventKind.PAYMENT,
    OrderEventKind.REFUND,
)


class RefundMethod(models.TextChoices):
    BKASH = "BKASH", _("bKash")
    CASH = "CASH", _("Cash")
    BANK = "BANK", _("Bank transfer")
    OTHER = "OTHER", _("Other")


class AttachmentKind(models.TextChoices):
    DESIGN = "DESIGN", _("Design")
    LOGO = "LOGO", _("Logo")
    REFERENCE = "REFERENCE", _("Reference")
    SIZE_GUIDE = "SIZE_GUIDE", _("Size guide")
    # Staff preview of the final print for the customer to check.
    MOCKUP = "MOCKUP", _("Mockup")
    OTHER = "OTHER", _("Other")


class AttachmentSource(models.TextChoices):
    CUSTOMER = "CUSTOMER", _("Customer")
    STAFF = "STAFF", _("Staff")
