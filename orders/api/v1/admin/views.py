from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.services.storage import is_in_folder, parse_url, upload_file
from app.utils.pagination import CustomPagination
from app.utils.permission import IsAdmin
from app.utils.phone import normalize_phone
from app.utils.response import APIResponse
from base.models import StoreConfig
from catalog.models import ProductVariant
from orders.api.v1.admin.serializers import (
    AdminCustomOrderQuoteSerializer,
    AdminOrderAttachmentCreateSerializer,
    AdminOrderAttachmentSerializer,
    AdminOrderBulkStatusSerializer,
    AdminOrderDetailsUpdateSerializer,
    AdminOrderEventSerializer,
    AdminOrderListSerializer,
    AdminOrderNoteSerializer,
    AdminOrderPaymentRecordSerializer,
    AdminOrderRefundSerializer,
    AdminOrderSerializer,
    AdminOrderStatusUpdateSerializer,
    AdminPaymentSerializer,
    AdminRefundSerializer,
)
from orders.models import Order, OrderAttachment, Payment
from orders.services import custom_orders as custom_services
from orders.services.orders import (
    OrderUpdateError,
    OutOfStockError,
    RefundError,
    add_order_note,
    advance_order_status,
    log_order_event,
    mark_order_paid_amount,
    record_refund,
    transition_order_status,
    update_order_details,
)
from orders.utils.choices import (
    OrderEventKind,
    OrderPaymentStatus,
    OrderStatus,
    OrderType,
    PaymentMethod,
    PaymentStatus,
)

MONEY = DecimalField(max_digits=14, decimal_places=2)
ZERO = Value(Decimal("0.00"), output_field=MONEY)


def _money(value):
    return f"{Decimal(value or 0).quantize(Decimal('0.01'))}"


def _paginated(view, request, queryset, serializer_class=None, message=""):
    paginator = view.pagination_class()
    page = paginator.paginate_queryset(queryset, request, view=view)
    data = (serializer_class(page, many=True).data if serializer_class else page)
    return APIResponse.success(
        data=data,
        meta={
            "count": paginator.page.paginator.count,
            "page": paginator.page.number,
            "page_size": paginator.get_page_size(request),
            "num_pages": paginator.page.paginator.num_pages,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
        },
        message=message,
    )


def _detail_queryset():
    return Order.objects.prefetch_related(
        "items",
        "attachments__created_by",
        "events__created_by",
        "payments",
        "refunds__created_by",
    ).select_related("placed_by")


def _order_detail_data(order):
    return AdminOrderSerializer(_detail_queryset().get(pk=order.pk)).data


ORDER_LIST_ORDERINGS = {
    "created_at",
    "-created_at",
    "total",
    "-total",
    "order_number",
    "-order_number",
    "updated_at",
    "-updated_at",
}


class AdminOrderListAPIView(GenericAPIView):
    """List orders for the admin panel with search and filters."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminOrderListSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = Order.objects.prefetch_related("items")

        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(order_number__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(customer_phone__icontains=search)
                | Q(tracking_number__icontains=search)
                | Q(items__sku__iexact=search)
            )

        # Exact, multi-value lookup: ?order_number=ARS-1,ARS-2
        order_numbers = [
            value.strip().upper()
            for value in params.get("order_number", "").split(",")
            if value.strip()
        ]
        if order_numbers:
            queryset = queryset.filter(order_number__in=order_numbers)

        for param, field in (
            ("order_type", "order_type"),
            ("status", "status"),
            ("payment_status", "payment_status"),
            ("payment_plan", "payment_plan"),
        ):
            values = [v.strip() for v in params.get(param, "").split(",") if v.strip()]
            if values:
                queryset = queryset.filter(**{f"{field}__in": values})

        phone = params.get("customer_phone", "").strip()
        if phone:
            queryset = queryset.filter(customer_phone=normalize_phone(phone))

        date_from = params.get("date_from", "").strip()
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = params.get("date_to", "").strip()
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        ordering = params.get("ordering", "-created_at").strip()
        if ordering not in ORDER_LIST_ORDERINGS:
            ordering = "-created_at"
        return queryset.order_by(ordering, "-created_at").distinct()

    def get(self, request, *args, **kwargs):
        return _paginated(
            self,
            request,
            self.get_queryset(),
            self.get_serializer_class(),
            message="Orders fetched successfully.",
        )


class _OrderObjectMixin:
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Order.objects.all()

    def _get_order(self, pk):
        return self.get_queryset().filter(pk=pk).first()

    def _not_found(self):
        return APIResponse.error(message="Order not found.", status=404)


class AdminOrderDetailAPIView(_OrderObjectMixin, GenericAPIView):
    """Retrieve a single order or move it through its lifecycle."""

    serializer_class = AdminOrderSerializer

    def get_queryset(self):
        return _detail_queryset()

    def get(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        return APIResponse.success(
            data=self.get_serializer(order).data,
            message="Order fetched successfully.",
        )

    def patch(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()

        serializer = AdminOrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            order = transition_order_status(
                order,
                data["status"],
                note=data["note"],
                created_by=request.user,
                courier=data.get("courier"),
                tracking_number=data.get("tracking_number"),
            )
        except OutOfStockError as exc:
            return APIResponse.error(message=str(exc), status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return APIResponse.error(message=str(exc))

        return APIResponse.success(
            data=_order_detail_data(order),
            message="Order status updated successfully.",
        )


class AdminOrderDetailsUpdateAPIView(_OrderObjectMixin, GenericAPIView):
    """Fix customer / shipping details (name, phone, address, city, courier)."""

    serializer_class = AdminOrderDetailsUpdateSerializer

    def patch(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        recalculate = data.pop("recalculate_delivery_fee", True)
        try:
            order = update_order_details(
                order,
                data,
                created_by=request.user,
                recalculate_delivery_fee=recalculate,
            )
        except OrderUpdateError as exc:
            return APIResponse.error(message=str(exc))
        return APIResponse.success(
            data=_order_detail_data(order),
            message="Order details updated successfully.",
        )


class AdminOrderNoteAPIView(_OrderObjectMixin, GenericAPIView):
    """Add an internal timeline note without changing the status."""

    serializer_class = AdminOrderNoteSerializer

    def post(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = add_order_note(
            order, serializer.validated_data["note"], created_by=request.user
        )
        return APIResponse.success(
            data=AdminOrderEventSerializer(event).data,
            message="Note added successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminOrderRefundAPIView(_OrderObjectMixin, GenericAPIView):
    """Record a refund for a cancelled paid order (optionally via bKash)."""

    serializer_class = AdminOrderRefundSerializer

    def post(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refund = record_refund(order, created_by=request.user, **serializer.validated_data)
        except RefundError as exc:
            return APIResponse.error(message=str(exc))
        return APIResponse.success(
            data={
                "refund": AdminRefundSerializer(refund).data,
                "order": _order_detail_data(order),
            },
            message="Refund recorded successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminCustomOrderQuoteAPIView(_OrderObjectMixin, GenericAPIView):
    """Send (or update) the final price of a custom order.

    Moves it from PENDING_REVIEW to PENDING_PAYMENT (or PENDING_CONFIRMATION
    for cash on delivery) so the customer can pay.
    """

    serializer_class = AdminCustomOrderQuoteSerializer

    def post(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            order = custom_services.quote_custom_order(
                order,
                items=data.get("items"),
                customization_fee=data.get("customization_fee"),
                discount=data.get("discount"),
                delivery_fee=data.get("delivery_fee"),
                payment_plan=data.get("payment_plan"),
                note=data["note"],
                created_by=request.user,
            )
        except custom_services.CustomOrderError as exc:
            errors = {exc.field: [str(exc)]} if exc.field else None
            return APIResponse.error(message=str(exc), errors=errors)
        except ValueError as exc:  # PaymentPlanError
            return APIResponse.error(message=str(exc), errors={"payment_plan": [str(exc)]})
        return APIResponse.success(
            data=_order_detail_data(order),
            message="Quote sent successfully.",
        )


class AdminOrderAttachmentListAPIView(_OrderObjectMixin, GenericAPIView):
    """List an order's files or attach a new one (e.g. a mockup for the customer)."""

    serializer_class = AdminOrderAttachmentCreateSerializer

    def get(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        attachments = order.attachments.select_related("created_by")
        return APIResponse.success(
            data=AdminOrderAttachmentSerializer(attachments, many=True).data,
            message="Attachments fetched successfully.",
        )

    def post(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data.get("file") is not None:
            uploaded_file = data["file"]
            try:
                upload = upload_file(
                    uploaded_file,
                    folder=f"{settings.CLOUDINARY_CUSTOM_ORDERS_FOLDER}/{order.order_number}",
                )
            except ImproperlyConfigured:
                return APIResponse.error(
                    message="File storage is not configured.",
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            url, public_id, resource_type = (
                upload["url"], upload["public_id"], upload["resource_type"]
            )
            file_name = data["file_name"] or Path(uploaded_file.name).name
        else:
            parsed = parse_url(data["url"])
            if parsed is None or not is_in_folder(
                parsed["public_id"], settings.CLOUDINARY_ROOT_FOLDER
            ):
                return APIResponse.error(
                    message="URL does not belong to this application's storage.",
                    errors={"url": ["URL does not belong to this application's storage."]},
                )
            url, public_id, resource_type = (
                data["url"], parsed["public_id"], parsed["resource_type"]
            )
            file_name = data["file_name"]

        attachment = custom_services.add_staff_attachment(
            order,
            url=url,
            public_id=public_id,
            resource_type=resource_type,
            kind=data["kind"],
            file_name=file_name,
            note=data["note"],
            created_by=request.user,
        )
        return APIResponse.success(
            data=AdminOrderAttachmentSerializer(attachment).data,
            message="Attachment added successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminOrderAttachmentDetailAPIView(_OrderObjectMixin, GenericAPIView):
    """Remove an attachment (its Cloudinary file is deleted too)."""

    def delete(self, request, pk, attachment_id, *args, **kwargs):
        attachment = OrderAttachment.objects.filter(pk=attachment_id, order_id=pk).first()
        if attachment is None:
            return APIResponse.error(message="Attachment not found.", status=404)
        with transaction.atomic():
            order = attachment.order
            label = attachment.file_name or attachment.get_kind_display()
            attachment.delete()
            log_order_event(
                order,
                f"Removed file {label}.",
                kind=OrderEventKind.EDIT,
                created_by=request.user,
            )
        return APIResponse.success(message="Attachment deleted successfully.")


class AdminOrderBulkStatusAPIView(GenericAPIView):
    """Move many orders to a status, stepping through intermediate states.

    Each order is updated in its own transaction; the response reports the
    outcome per id.
    """

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminOrderBulkStatusSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        shipping = {
            key: data[key] for key in ("courier", "tracking_number") if key in data
        }

        ids = list(dict.fromkeys(data["ids"]))
        orders = {order.pk: order for order in Order.objects.filter(pk__in=ids)}
        results = []
        for order_id in ids:
            order = orders.get(order_id)
            if order is None:
                results.append({"id": str(order_id), "ok": False, "error": "Order not found."})
                continue
            try:
                with transaction.atomic():
                    advance_order_status(
                        order,
                        data["status"],
                        note=data["note"],
                        created_by=request.user,
                        **shipping,
                    )
                results.append(
                    {
                        "id": str(order_id),
                        "order_number": order.order_number,
                        "ok": True,
                        "status": order.status,
                        "error": None,
                    }
                )
            except ValueError as exc:
                order.refresh_from_db()
                results.append(
                    {
                        "id": str(order_id),
                        "order_number": order.order_number,
                        "ok": False,
                        "status": order.status,
                        "error": str(exc),
                    }
                )

        succeeded = sum(1 for result in results if result["ok"])
        return APIResponse.success(
            data={
                "results": results,
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            },
            message=f"{succeeded} of {len(results)} orders updated.",
        )


class AdminOrderPaymentRecordAPIView(_OrderObjectMixin, GenericAPIView):
    """Record an offline payment (cash on delivery, due half collection)."""

    serializer_class = AdminOrderPaymentRecordSerializer

    def post(self, request, pk, *args, **kwargs):
        order = self._get_order(pk)
        if order is None:
            return self._not_found()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        amount = serializer.validated_data["amount"]
        note = serializer.validated_data["note"]

        if order.status == OrderStatus.CANCELLED:
            return APIResponse.error(message="This order has been cancelled.")
        if amount > order.due_amount:
            return APIResponse.error(
                message=f"Amount exceeds the due balance of {order.due_amount} BDT."
            )

        with transaction.atomic():
            mark_order_paid_amount(order, amount)
            Payment.objects.create(
                order=order,
                method=PaymentMethod.COD,
                plan=order.payment_plan,
                amount=amount,
                status=PaymentStatus.COMPLETED,
                completed_at=timezone.now(),
                metadata={"recorded_by": str(request.user.pk), "note": note},
            )
            message = f"Offline payment recorded: {amount} BDT."
            if note:
                message += f" {note}"
            log_order_event(order, message, kind=OrderEventKind.PAYMENT, created_by=request.user)

        return APIResponse.success(
            data=_order_detail_data(order),
            message="Payment recorded successfully.",
            status=status.HTTP_201_CREATED,
        )


class AdminPaymentListAPIView(GenericAPIView):
    """List all payment attempts."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminPaymentSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = Payment.objects.select_related("order").order_by("-created_at")

        params = self.request.query_params
        payment_status = params.get("status", "").strip()
        if payment_status:
            queryset = queryset.filter(status=payment_status)
        method = params.get("method", "").strip()
        if method:
            queryset = queryset.filter(method=method)
        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(order__order_number__icontains=search)
                | Q(bkash_trx_id__icontains=search)
                | Q(bkash_payment_id__icontains=search)
            )
        return queryset

    def get(self, request, *args, **kwargs):
        return _paginated(
            self,
            request,
            self.get_queryset(),
            self.get_serializer_class(),
            message="Payments fetched successfully.",
        )


CUSTOMER_ORDERINGS = {
    "last_order_at": "last_order_at",
    "-last_order_at": "-last_order_at",
    "lifetime_value": "lifetime_value",
    "-lifetime_value": "-lifetime_value",
    "order_count": "order_count",
    "-order_count": "-order_count",
}


class AdminCustomerListAPIView(GenericAPIView):
    """Guest customers grouped by phone with order count and lifetime value."""

    permission_classes = [IsAuthenticated, IsAdmin]
    pagination_class = CustomPagination

    def get_queryset(self):
        orders = Order.objects.exclude(customer_phone="")
        search = self.request.query_params.get("search", "").strip()
        if search:
            orders = orders.filter(
                Q(customer_phone__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
            )

        latest = Order.objects.filter(customer_phone=OuterRef("customer_phone")).order_by(
            "-created_at"
        )
        not_cancelled = ~Q(status=OrderStatus.CANCELLED)
        queryset = (
            orders.values("customer_phone")
            .annotate(
                customer_name=Subquery(latest.values("customer_name")[:1]),
                customer_email=Subquery(latest.values("customer_email")[:1]),
                city=Subquery(latest.values("city")[:1]),
                order_count=Count("id"),
                completed_orders=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
                cancelled_orders=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
                lifetime_value=Coalesce(Sum("total", filter=not_cancelled), ZERO),
                total_paid=Coalesce(Sum(F("paid_amount") - F("refunded_amount")), ZERO),
                first_order_at=Min("created_at"),
                last_order_at=Max("created_at"),
            )
        )
        ordering = CUSTOMER_ORDERINGS.get(
            self.request.query_params.get("ordering", "").strip(), "-last_order_at"
        )
        return queryset.order_by(ordering, "customer_phone")

    def get(self, request, *args, **kwargs):
        response = _paginated(
            self, request, self.get_queryset(), message="Customers fetched successfully."
        )
        for row in response.data["data"]:
            row["lifetime_value"] = _money(row["lifetime_value"])
            row["total_paid"] = _money(row["total_paid"])
        return response


def _parse_date(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


class AdminStatsAPIView(GenericAPIView):
    """Dashboard totals for a date range (defaults to the current month)."""

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        params = request.query_params
        date_from = _parse_date(params.get("date_from"), today.replace(day=1))
        date_to = _parse_date(params.get("date_to"), today)
        if date_from is None or date_to is None:
            return APIResponse.error(
                message="Dates must use the YYYY-MM-DD format.",
                errors={"date_from": ["Use YYYY-MM-DD."], "date_to": ["Use YYYY-MM-DD."]},
            )
        if date_from > date_to:
            return APIResponse.error(message="date_from must be on or before date_to.")
        if (date_to - date_from).days > 366:
            return APIResponse.error(message="The date range cannot exceed 366 days.")

        config = StoreConfig.objects.first()
        threshold_param = params.get("low_stock_threshold", "").strip()
        threshold = (
            int(threshold_param)
            if threshold_param.isdigit()
            else (config.low_stock_threshold if config else 3)
        )

        orders = Order.objects.filter(
            created_at__date__gte=date_from, created_at__date__lte=date_to
        )
        not_cancelled = ~Q(status=OrderStatus.CANCELLED)
        active = orders.filter(not_cancelled)

        by_status = {value: 0 for value, _ in OrderStatus.choices}
        for row in orders.values("status").annotate(count=Count("id")):
            by_status[row["status"]] = row["count"]

        by_plan = {}
        for row in active.values("payment_plan").annotate(count=Count("id")):
            by_plan[row["payment_plan"]] = row["count"]

        by_type = {value: 0 for value, _ in OrderType.choices}
        for row in active.values("order_type").annotate(count=Count("id")):
            by_type[row["order_type"]] = row["count"]
        # Custom orders waiting for a quote, whenever they were placed.
        awaiting_quote = Order.objects.filter(status=OrderStatus.PENDING_REVIEW).count()

        totals = active.aggregate(
            count=Count("id"),
            gross=Coalesce(Sum("total"), ZERO),
            outstanding=Coalesce(
                Sum(
                    Case(
                        When(total__gt=F("paid_amount"), then=F("total") - F("paid_amount")),
                        default=ZERO,
                        output_field=MONEY,
                    )
                ),
                ZERO,
            ),
        )
        collected = orders.aggregate(
            value=Coalesce(Sum(F("paid_amount") - F("refunded_amount")), ZERO)
        )["value"]
        refunds_due = orders.filter(
            payment_status=OrderPaymentStatus.REFUND_PENDING
        ).aggregate(value=Coalesce(Sum(F("paid_amount") - F("refunded_amount")), ZERO))[
            "value"
        ]
        aov = (totals["gross"] / totals["count"]) if totals["count"] else Decimal("0")

        daily_rows = {
            row["day"]: row
            for row in orders.annotate(
                day=TruncDate("created_at", tzinfo=timezone.get_current_timezone())
            )
            .values("day")
            .annotate(
                orders=Count("id"),
                cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
                revenue=Coalesce(Sum("total", filter=not_cancelled), ZERO),
            )
        }
        daily = []
        day = date_from
        while day <= date_to:
            row = daily_rows.get(day)
            daily.append(
                {
                    "date": day.isoformat(),
                    "orders": row["orders"] if row else 0,
                    "cancelled": row["cancelled"] if row else 0,
                    "revenue": _money(row["revenue"] if row else 0),
                }
            )
            day += timedelta(days=1)

        low_stock = [
            {
                "product_id": str(variant.product_id),
                "product_name": variant.product.name,
                "product_slug": variant.product.slug,
                "variant_id": str(variant.pk),
                "sku": variant.sku,
                "display_name": variant.display_name,
                "stock": variant.stock,
            }
            for variant in ProductVariant.objects.select_related("product")
            .filter(is_active=True, stock__lte=threshold)
            .exclude(product__status="ARCHIVED")
            .order_by("stock", "product__name")[:100]
        ]

        return APIResponse.success(
            data={
                "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
                "orders": {
                    "count": orders.count(),
                    "active": totals["count"],
                    "by_status": by_status,
                    "by_payment_plan": by_plan,
                    "by_order_type": by_type,
                    "custom_awaiting_quote": awaiting_quote,
                },
                "revenue": {
                    "gross": _money(totals["gross"]),
                    "collected": _money(collected),
                    "outstanding_cod": _money(totals["outstanding"]),
                    "refunds_due": _money(refunds_due),
                    "aov": _money(aov),
                },
                "daily": daily,
                "low_stock_threshold": threshold,
                "low_stock": low_stock,
            },
            message="Stats fetched successfully.",
        )
