from django.db.models.signals import post_delete
from django.dispatch import receiver

from app.services.storage import schedule_delete_image
from orders.models import OrderAttachment


@receiver(post_delete, sender=OrderAttachment)
def delete_attachment_file(sender, instance, **kwargs):
    """Remove the Cloudinary file once its attachment row is gone."""
    schedule_delete_image(
        public_id=instance.public_id, resource_type=instance.resource_type
    )
