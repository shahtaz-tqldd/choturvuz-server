from uuid import uuid4
from django.db import models
from django.conf import settings


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseUserModel(BaseModel):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_created_records",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated_records",
    )

    class Meta:
        abstract = True



class BaseImage(BaseModel):
    image_url = models.URLField()
    caption = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True
