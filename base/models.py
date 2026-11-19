from django.conf import settings
from django.db import models


class StoreConfig(models.Model):
    """Singleton holding store-wide branding, policy and commerce defaults."""

    # Branding
    title = models.CharField(max_length=200, default="Choturvuz")
    tagline = models.CharField(max_length=300, blank=True)
    logo_url = models.URLField(blank=True)
    favicon_url = models.URLField(blank=True)
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=20, blank=True)

    # Legal / Policy
    privacy_policy = models.TextField(blank=True)
    terms_of_service = models.TextField(blank=True)
    return_policy = models.TextField(blank=True)
    data_deletion_policy = models.TextField(blank=True)

    # Feature flags
    maintenance_mode = models.BooleanField(default=False)

    # Announcement
    notify_banner_enabled = models.BooleanField(default=False)
    notify_banner_text = models.CharField(max_length=500, blank=True)
    notify_banner_url = models.URLField(blank=True)

    # Commerce defaults
    currency = models.CharField(max_length=8, default="BDT")
    delivery_fee_inside_dhaka = models.DecimalField(
        max_digits=10, decimal_places=2, default=70
    )
    delivery_fee_outside_dhaka = models.DecimalField(
        max_digits=10, decimal_places=2, default=130
    )
    allow_half_payment = models.BooleanField(
        default=True, help_text="Let customers pay 50% advance and the rest on delivery."
    )
    allow_cod = models.BooleanField(
        default=True, help_text="Let customers order with cash on delivery."
    )
    free_delivery_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Orders with a subtotal at or above this get free delivery. Empty disables it.",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=3, help_text="Variants at or below this stock count as low stock."
    )

    # SEO / public metadata
    meta_title = models.CharField(max_length=200, blank=True)
    meta_description = models.TextField(blank=True)

    # Set the first time `seed_demo --once` runs, so demo data is loaded at
    # most once (deleting it later does not bring it back on restart).
    demo_seeded_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated_records",
    )

    class Meta:
        verbose_name = "Store Configuration"
        verbose_name_plural = "Store Configuration"

    def __str__(self):
        return self.title or "Store Configuration"

    def save(self, *args, **kwargs):
        # Keep this model as a singleton
        if not self.pk and StoreConfig.objects.exists():
            raise ValueError("Only one StoreConfig instance is allowed.")

        super().save(*args, **kwargs)
