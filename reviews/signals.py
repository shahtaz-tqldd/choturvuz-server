from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from app.services.revalidate import REVIEWS, queue_revalidation
from reviews.models import Review


@receiver(post_save, sender=Review)
@receiver(post_delete, sender=Review)
def review_changed(sender, instance, **kwargs):
    queue_revalidation(REVIEWS)
