from django.contrib import admin

from base.models import StoreConfig


@admin.register(StoreConfig)
class StoreConfigAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "support_email",
        "maintenance_mode",
        "notify_banner_enabled",
        "updated_at",
    )
    readonly_fields = ("updated_by", "created_at", "updated_at")
