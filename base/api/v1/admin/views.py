from django.db import transaction
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.utils.permission import IsAdmin
from app.utils.response import APIResponse

from base.api.v1.utils import (
    CONFIG_SECTIONS,
    STAFF_EDITABLE_SECTIONS,
    serialize_config_sections,
)
from base.api.v1.admin.serializers import StoreConfigUpdateSerializer
from base.models import StoreConfig


def get_config():
    config = StoreConfig.objects.first()
    if config is None:
        config = StoreConfig.objects.create()
    return config


class StoreConfigUpdateAPIView(GenericAPIView):
    """Update the singleton configuration, including multipart logo uploads."""

    permission_classes = [IsAuthenticated, IsAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = StoreConfigUpdateSerializer

    @staticmethod
    def _touched_fields(data):
        fields = set()
        for key in data.keys():
            if key in CONFIG_SECTIONS:
                section = data[key]
                if hasattr(section, "keys"):
                    fields.update(section.keys())
            else:
                fields.add(key)
        return fields

    def _staff_may_edit(self, request):
        allowed = {
            field
            for section in STAFF_EDITABLE_SECTIONS
            for field in CONFIG_SECTIONS[section]
        }
        touched = self._touched_fields(request.data)
        return bool(touched) and touched <= allowed

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        if not request.user.is_superuser and not self._staff_may_edit(request):
            return APIResponse.error(
                message=(
                    "Only superadmin users can change these settings. "
                    "Staff can update the announcement banner."
                ),
                status=403,
            )
        config = get_config()
        serializer = self.get_serializer(
            config,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        return APIResponse.success(
            data=serialize_config_sections(config, list(CONFIG_SECTIONS)),
            message="Configuration updated successfully.",
        )
