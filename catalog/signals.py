"""Queue storefront cache revalidation when catalog data changes.

Queryset .update() calls skip these signals; code that uses them (bulk
actions, stock changes) queues revalidation itself.
"""

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from app.services.revalidate import (
    COLLECTIONS,
    PRODUCTS,
    STORE_CONFIG,
    product_tag,
    queue_revalidation,
)
from catalog.models import Collection, Product, ProductImage, ProductVariant


@receiver(pre_save, sender=Product)
def remember_previous_slug(sender, instance, **kwargs):
    if instance.pk:
        instance._previous_slug = (
            Product.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
        )


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def product_changed(sender, instance, **kwargs):
    queue_revalidation(
        PRODUCTS,
        product_tag(instance.slug),
        product_tag(getattr(instance, "_previous_slug", None)),
    )


@receiver(post_save, sender=ProductVariant)
@receiver(post_delete, sender=ProductVariant)
@receiver(post_save, sender=ProductImage)
@receiver(post_delete, sender=ProductImage)
def product_child_changed(sender, instance, **kwargs):
    slug = (
        Product.objects.filter(pk=instance.product_id).values_list("slug", flat=True).first()
    )
    # Images and variants feed the product cards too (image, in_stock).
    queue_revalidation(PRODUCTS, product_tag(slug))


@receiver(post_save, sender=Collection)
@receiver(post_delete, sender=Collection)
def collection_changed(sender, instance, **kwargs):
    queue_revalidation(COLLECTIONS, PRODUCTS)


@receiver(m2m_changed, sender=Product.collections.through)
def product_collections_changed(sender, instance, action, pk_set, **kwargs):
    if not action.startswith("post_"):
        return
    queue_revalidation(COLLECTIONS, PRODUCTS)


@receiver(post_save, sender="base.StoreConfig")
def store_config_changed(sender, instance, **kwargs):
    queue_revalidation(STORE_CONFIG)
