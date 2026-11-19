from django.contrib import admin

from reviews.models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("customer_name", "rating", "status", "is_featured", "source", "created_at")
    list_filter = ("status", "rating", "is_featured", "source")
    search_fields = ("customer_name", "customer_email", "customer_phone", "title", "comment")
    readonly_fields = ("moderated_by", "moderated_at", "published_at", "created_at", "updated_at")
    raw_id_fields = ("product", "order")
