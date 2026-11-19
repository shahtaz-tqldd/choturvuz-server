from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.serializers import ValidationError

from app.services.storage import CloudinaryStorage, is_in_folder, parse_url, upload_file
from app.utils.permission import IsAdmin
from app.utils.response import APIResponse
from base.api.v1.client.serializers import (
    FileDeleteSerializer,
    FileRetrieveSerializer,
    FileUploadSerializer,
)
from base.api.v1.utils import (
    CONFIG_SECTIONS,
    LEGAL_DOCUMENT_TYPES,
    serialize_config_sections,
)

from base.models import StoreConfig


def get_config():
    config = StoreConfig.objects.first()
    if config is None:
        config = StoreConfig.objects.create()
    return config


def resolve_file_key(*, key=None, url=None, resource_type="image"):
    """Return (public_id, resource_type) for an asset this app owns."""
    if url and not key:
        parsed = parse_url(url)
        if parsed is None:
            raise ValidationError(
                {"url": ["URL does not belong to this application's storage."]}
            )
        key, resource_type = parsed["public_id"], parsed["resource_type"]
    if not key:
        raise ValidationError({"key": ["Provide a key or url."]})
    if not is_in_folder(key, settings.CLOUDINARY_ROOT_FOLDER):
        raise ValidationError({"key": ["This file key is not permitted."]})
    return key, resource_type


class StoreConfigAPIView(GenericAPIView):
    """Return all configuration sections or the sections selected by query param."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        selected_sections = [
            section for section in CONFIG_SECTIONS if section in request.query_params
        ]
        document_type = request.query_params.get("document_type")

        if document_type and document_type not in LEGAL_DOCUMENT_TYPES:
            raise ValidationError(
                {"document_type": f"Choose one of: {', '.join(LEGAL_DOCUMENT_TYPES)}."}
            )

        if document_type and "legal_document" not in selected_sections:
            selected_sections.append("legal_document")

        if not selected_sections:
            selected_sections = list(CONFIG_SECTIONS)

        config = get_config()
        return APIResponse.success(
            data=serialize_config_sections(config, selected_sections, document_type),
            message="Configuration fetched successfully.",
        )


class RetrieveFileAPIView(GenericAPIView):
    """Return the delivery URL (optionally a forced-download URL) for a stored file."""

    permission_classes = [IsAuthenticated]
    serializer_class = FileRetrieveSerializer

    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        key, resource_type = resolve_file_key(
            key=serializer.validated_data.get("key"),
            url=serializer.validated_data.get("url"),
            resource_type=serializer.validated_data.get("resource_type") or "image",
        )
        try:
            download_file_name = None
            if serializer.validated_data.get("download"):
                download_file_name = (
                    serializer.validated_data.get("file_name")
                    or Path(key).name
                )
            url = CloudinaryStorage().delivery_url(
                key,
                resource_type=resource_type,
                download_file_name=download_file_name,
            )
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={"key": key, "resource_type": resource_type, "url": url},
            message="File fetched successfully.",
        )


class UploadFileAPIView(GenericAPIView):
    """Upload a file to storage and return its permanent URL and key (staff only)."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = FileUploadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        try:
            upload = upload_file(uploaded_file)
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={
                "file_url": upload["url"],
                "key": upload["key"],
                "resource_type": upload["resource_type"],
                "file_name": Path(uploaded_file.name).name,
                "content_type": upload["content_type"],
                "file_size": uploaded_file.size,
            },
            message="File uploaded successfully.",
            status=status.HTTP_201_CREATED,
        )


class DeleteFileAPIView(GenericAPIView):
    """Delete a stored file by key or URL (staff only)."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = FileDeleteSerializer

    def delete(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data or request.query_params)
        serializer.is_valid(raise_exception=True)
        key, resource_type = resolve_file_key(
            key=serializer.validated_data.get("key"),
            url=serializer.validated_data.get("url"),
            resource_type=serializer.validated_data.get("resource_type") or "image",
        )
        try:
            CloudinaryStorage().delete(key, resource_type=resource_type)
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={"key": key},
            message="File deleted successfully.",
        )
