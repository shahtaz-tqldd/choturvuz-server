from django.urls import path

from orders.api.v1.admin import views

urlpatterns = [
    path("", views.AdminOrderListAPIView.as_view(), name="admin-order-list"),
    path(
        "bulk-status/",
        views.AdminOrderBulkStatusAPIView.as_view(),
        name="admin-order-bulk-status",
    ),
    path(
        "customers/",
        views.AdminCustomerListAPIView.as_view(),
        name="admin-order-customers",
    ),
    path(
        "<uuid:pk>/",
        views.AdminOrderDetailAPIView.as_view(),
        name="admin-order-detail",
    ),
    path(
        "<uuid:pk>/customer/",
        views.AdminOrderDetailsUpdateAPIView.as_view(),
        name="admin-order-customer-update",
    ),
    path(
        "<uuid:pk>/notes/",
        views.AdminOrderNoteAPIView.as_view(),
        name="admin-order-notes",
    ),
    path(
        "<uuid:pk>/refund/",
        views.AdminOrderRefundAPIView.as_view(),
        name="admin-order-refund",
    ),
    path(
        "<uuid:pk>/quote/",
        views.AdminCustomOrderQuoteAPIView.as_view(),
        name="admin-custom-order-quote",
    ),
    path(
        "<uuid:pk>/attachments/",
        views.AdminOrderAttachmentListAPIView.as_view(),
        name="admin-order-attachments",
    ),
    path(
        "<uuid:pk>/attachments/<uuid:attachment_id>/",
        views.AdminOrderAttachmentDetailAPIView.as_view(),
        name="admin-order-attachment-detail",
    ),
    path(
        "<uuid:pk>/record-payment/",
        views.AdminOrderPaymentRecordAPIView.as_view(),
        name="admin-order-record-payment",
    ),
]
