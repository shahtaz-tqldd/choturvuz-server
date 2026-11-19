from django.db import models
from django.utils.translation import gettext_lazy as _


class ReviewStatus(models.TextChoices):
    # Waiting for staff; never shown on the site.
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    HIDDEN = "HIDDEN", _("Hidden")


class ReviewSource(models.TextChoices):
    CUSTOMER = "CUSTOMER", _("Customer")
    STAFF = "STAFF", _("Staff")
