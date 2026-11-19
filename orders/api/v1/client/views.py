from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from app.services.storage import upload_file
from app.utils.phone import normalize_phone
from app.utils.response import APIResponse
from orders.api.v1.serializers import (
    CustomOrderAddAttachmentsSerializer,
    CustomOrderCreateSerializer,
    CustomOrderEstimateResultSerializer,
    CustomOrderEstimateSerializer,
    CustomOrderUploadSerializer,
    OrderAttachmentSerializer,
    OrderCreateSerializer,
    OrderPaySerializer,
    OrderQuoteResultSerializer,
    OrderQuoteSerializer,
    OrderSerializer,
    OrderTrackSerializer,
)
from orders.api.v1.throttles import (
    CustomUploadIPThrottle,
    OrderCreateIPThrottle,
    OrderCreatePhoneThrottle,
)
from orders.models import Order
from orders.services import custom_orders as custom_services
from orders.services import payments as payment_services
from orders.services.orders import (
    OutOfStockError,
    PaymentPlanError,
    check_payment_plan,
    create_order,
    quote_order,
)
from orders.utils.choices import PaymentPlan


def _order_queryset():
    return Order.objects.prefetch_related(
        "items", "attachments", "events", "payments", "refunds"
    )


def _find_customer_order(order_number, email="", phone=""):
    """The order, only when the email/phone matches the one used to place it."""
    order = _order_queryset().filter(order_number=(order_number or "").strip().upper()).first()
    if order is None:
        return None
    email = (email or "").strip().lower()
    phone = normalize_phone(phone or "")
    authorized = bool(
        (email and order.customer_email and order.customer_email == email)
        or (
            phone
            and order.customer_phone
            and normalize_phone(order.customer_phone) == phone
        )
    )
    return order if authorized else None


def _custom_error(exc):
    errors = {exc.field: [str(exc)]} if getattr(exc, "field", None) else None
    return APIResponse.error(message=str(exc), errors=errors)

User = get_user_model()


def _customer_data(validated):
    return {
        "customer_name": validated["customer_name"],
        "customer_email": validated["customer_email"].strip().lower(),
        "customer_phone": normalize_phone(validated["customer_phone"]),
        "address_line": validated["address_line"],
        "city": validated["city"],
        "postal_code": validated.get("postal_code", ""),
        "notes": validated.get("notes", ""),
    }


class OrderCreateAPIView(GenericAPIView):
    """Place an order as a guest; email or phone identifies the customer."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = OrderCreateSerializer
    throttle_classes = [OrderCreateIPThrottle, OrderCreatePhoneThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        placed_by = (
            request.user
            if request.user.is_authenticated
            and isinstance(request.user, User)
            else None
        )

        try:
            order = create_order(
                customer_data=_customer_data(data),
                items_data=data["merged_items"],
                payment_plan=data["payment_plan"],
                placed_by=placed_by,
            )
        except OutOfStockError as exc:
            return APIResponse.error(message=str(exc), status=status.HTTP_409_CONFLICT)
        except PaymentPlanError as exc:
            return APIResponse.error(
                message=str(exc), errors={"payment_plan": [str(exc)]}
            )

        message = (
            "Order placed successfully. We will call you to confirm it."
            if order.payment_plan == PaymentPlan.COD
            else "Order placed successfully. Complete the payment to confirm it."
        )
        return APIResponse.success(
            data=OrderSerializer(_order_queryset().get(pk=order.pk)).data,
            message=message,
            status=status.HTTP_201_CREATED,
        )


class OrderQuoteAPIView(GenericAPIView):
    """Price a cart (totals, delivery fee, advance) and flag unavailable lines."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = OrderQuoteSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            check_payment_plan(data["payment_plan"])
        except PaymentPlanError as exc:
            return APIResponse.error(
                message=str(exc), errors={"payment_plan": [str(exc)]}
            )
        quote = quote_order(
            items_data=data["items"],
            city=data["city"],
            payment_plan=data["payment_plan"],
        )
        return APIResponse.success(
            data=OrderQuoteResultSerializer(quote).data,
            message="Quote calculated successfully.",
        )


class OrderTrackAPIView(GenericAPIView):
    """Track an order with its order number plus the email/phone used."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = OrderTrackSerializer

    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        order = _find_customer_order(
            data["order_number"], email=data.get("email", ""), phone=data.get("phone", "")
        )

        if order is None:
            return APIResponse.error(
                message="No order found for that order number and email/phone.",
                status=404,
            )

        return APIResponse.success(
            data=OrderSerializer(order).data,
            message="Order fetched successfully.",
        )


class CustomOrderUploadAPIView(GenericAPIView):
    """Upload one design/logo/reference file for a custom order (no auth).

    Returns a URL to send in `attachments` when placing the custom order.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CustomOrderUploadSerializer
    throttle_classes = [CustomUploadIPThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        folder = (
            f"{settings.CLOUDINARY_CUSTOM_ORDERS_FOLDER}/uploads/"
            f"{timezone.localdate():%Y-%m}"
        )
        try:
            upload = upload_file(uploaded_file, folder=folder)
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={
                "url": upload["url"],
                "public_id": upload["public_id"],
                "resource_type": upload["resource_type"],
                "format": upload["format"],
                "file_name": Path(uploaded_file.name).name,
                "content_type": upload["content_type"],
                "file_size": uploaded_file.size,
            },
            message="File uploaded successfully.",
            status=status.HTTP_201_CREATED,
        )


class CustomOrderEstimateAPIView(GenericAPIView):
    """Indicative price for a custom order before it is placed."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CustomOrderEstimateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            check_payment_plan(data["payment_plan"])
            template = custom_services.get_custom_template(data["template_id"])
            lines = custom_services.build_lines(template, data["items"])
        except PaymentPlanError as exc:
            return APIResponse.error(message=str(exc), errors={"payment_plan": [str(exc)]})
        except custom_services.CustomOrderError as exc:
            return _custom_error(exc)
        estimate = custom_services.estimate_custom_order(
            template=template,
            lines=lines,
            city=data["city"],
            payment_plan=data["payment_plan"],
        )
        return APIResponse.success(
            data=CustomOrderEstimateResultSerializer(estimate).data,
            message="Estimate calculated successfully.",
        )


class CustomOrderCreateAPIView(GenericAPIView):
    """Place a custom order (guest). It waits for a staff quote before payment."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CustomOrderCreateSerializer
    throttle_classes = [OrderCreateIPThrottle, OrderCreatePhoneThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        placed_by = (
            request.user
            if request.user.is_authenticated and isinstance(request.user, User)
            else None
        )
        try:
            template = custom_services.get_custom_template(data["template_id"])
            lines = custom_services.build_lines(template, data["items"])
            order = custom_services.create_custom_order(
                customer_data=_customer_data(data),
                template=template,
                lines=lines,
                attachments_data=data["attachments"],
                custom_instructions=data["custom_instructions"],
                size_guidelines=data["size_guidelines"],
                needed_by=data["needed_by"],
                payment_plan=data["payment_plan"],
                placed_by=placed_by,
            )
        except PaymentPlanError as exc:
            return APIResponse.error(message=str(exc), errors={"payment_plan": [str(exc)]})
        except custom_services.CustomOrderError as exc:
            return _custom_error(exc)

        return APIResponse.success(
            data=OrderSerializer(_order_queryset().get(pk=order.pk)).data,
            message=(
                "Custom order received. We will review your design and send "
                "the final price shortly."
            ),
            status=status.HTTP_201_CREATED,
        )


class CustomOrderAttachmentsAPIView(GenericAPIView):
    """Customer adds more files to their custom order (email/phone required)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CustomOrderAddAttachmentsSerializer
    throttle_classes = [CustomUploadIPThrottle]

    def post(self, request, order_number, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = _find_customer_order(order_number, email=data["email"], phone=data["phone"])
        if order is None:
            return APIResponse.error(
                message="No order found for that order number and email/phone.",
                status=404,
            )
        try:
            created = custom_services.add_customer_attachments(order, data["attachments"])
        except custom_services.CustomOrderError as exc:
            return _custom_error(exc)
        return APIResponse.success(
            data={
                "added": OrderAttachmentSerializer(created, many=True).data,
                "order": OrderSerializer(_order_queryset().get(pk=order.pk)).data,
            },
            message="Files added successfully.",
            status=status.HTTP_201_CREATED,
        )


class OrderPayAPIView(GenericAPIView):
    """Start a bKash payment for the amount due on an order."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = OrderPaySerializer

    def post(self, request, order_number, *args, **kwargs):
        order = Order.objects.filter(order_number=order_number.strip().upper()).first()
        if order is None:
            return APIResponse.error(message="Order not found.", status=404)

        try:
            result = payment_services.initiate_bkash_payment(order)
        except payment_services.PaymentError as exc:
            return APIResponse.error(message=str(exc))

        return APIResponse.success(
            data=result,
            message="Payment initiated. Redirect the customer to payment_url.",
        )


class BkashCallbackAPIView(GenericAPIView):
    """Receive the customer back from bKash, capture the money, and bounce
    them to the storefront order-tracking page."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        payment_id = request.query_params.get("paymentID", "")
        bkash_status = request.query_params.get("status", "").lower()

        order = (
            Order.objects.filter(payments__bkash_payment_id=payment_id).first()
            if payment_id
            else None
        )
        redirect_base = f"{settings.USER_FRONTEND_URL.rstrip('/')}/orders/track"

        if order is None:
            return redirect(f"{redirect_base}?payment=unknown")

        query = f"order_number={order.order_number}"

        if bkash_status == "success":
            try:
                payment_services.execute_bkash_payment(payment_id)
                return redirect(f"{redirect_base}?{query}&payment=success")
            except payment_services.PaymentError:
                return redirect(f"{redirect_base}?{query}&payment=failed")

        if bkash_status == "cancel":
            payment_services.fail_bkash_payment(
                bkash_payment_id=payment_id,
                status="CANCELLED",
                reason="Customer cancelled the bKash payment.",
            )
            return redirect(f"{redirect_base}?{query}&payment=cancelled")

        payment_services.fail_bkash_payment(
            bkash_payment_id=payment_id,
            status="FAILED",
            reason="bKash reported a failed payment.",
        )
        return redirect(f"{redirect_base}?{query}&payment=failed")
