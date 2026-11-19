CONFIG_SECTIONS = {
    "branding": (
        "title",
        "tagline",
        "logo",
        "favicon",
        "support_email",
        "support_phone",
    ),
    "legal_document": (
        "privacy_policy",
        "terms_of_service",
        "return_policy",
        "data_deletion_policy",
    ),
    "feature": ("maintenance_mode",),
    "announcement": (
        "notify_banner_enabled",
        "notify_banner_text",
        "notify_banner_url",
    ),
    "commerce": (
        "currency",
        "delivery_fee_inside_dhaka",
        "delivery_fee_outside_dhaka",
        "allow_half_payment",
        "allow_cod",
        "free_delivery_threshold",
        "low_stock_threshold",
    ),
    "seo": ("meta_title", "meta_description"),
    "audit": ("created_at", "updated_at", "updated_by"),
}

LEGAL_DOCUMENT_TYPES = CONFIG_SECTIONS["legal_document"]

# Sections regular staff (non-superusers) may update.
STAFF_EDITABLE_SECTIONS = ("announcement",)

FIELD_OVERRIDES = {"logo": "logo_url", "favicon": "favicon_url"}


def serialize_config_sections(instance, sections, document_type=None):
    data = {}
    for section in sections:
        fields = CONFIG_SECTIONS[section]
        if section == "legal_document" and document_type:
            fields = (document_type,)

        values = {}
        for field in fields:
            value = getattr(instance, FIELD_OVERRIDES.get(field, field))
            if field == "updated_by":
                value = str(value.pk) if value else None
            elif field in ("created_at", "updated_at"):
                value = value.isoformat() if value else None
            values[field] = value
        data[section] = values
    return data
