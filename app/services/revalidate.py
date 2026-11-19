"""Tell the storefront (Next.js) which cached pages to rebuild.

POST {STOREFRONT_REVALIDATE_URL}
x-revalidate-secret: <STOREFRONT_REVALIDATE_SECRET>
{"tags": ["products", "product:<slug>", "collections", "store-config", "reviews"]}

Tags are collected during a transaction and sent once it commits, through a
Celery task, so a rolled-back change is never announced on its own and a
request never waits on the storefront.
"""

from django.conf import settings
from django.db import transaction

from app.utils.logger import logger

PRODUCTS = "products"
COLLECTIONS = "collections"
STORE_CONFIG = "store-config"
REVIEWS = "reviews"


def product_tag(slug):
    return f"product:{slug}" if slug else ""


def is_enabled():
    return bool(
        getattr(settings, "STOREFRONT_REVALIDATE_URL", "")
        and getattr(settings, "STOREFRONT_REVALIDATE_SECRET", "")
    )


def _pending(connection):
    pending = getattr(connection, "_storefront_revalidate_tags", None)
    if pending is None:
        pending = set()
        connection._storefront_revalidate_tags = pending
    return pending


def _flush(connection):
    pending = _pending(connection)
    if not pending:
        return
    tags = sorted(pending)
    pending.clear()
    _dispatch(tags)


def _dispatch(tags):
    from app.tasks import send_storefront_revalidation

    try:
        send_storefront_revalidation.delay(tags)
    except Exception:  # broker down must never break the request
        logger.warning("Could not queue storefront revalidation for %s", tags, exc_info=True)


def queue_revalidation(*tags):
    """Revalidate these cache tags after the current transaction commits."""
    tags = {tag for tag in tags if tag}
    if not tags or not is_enabled():
        return
    connection = transaction.get_connection()
    _pending(connection).update(tags)
    # Several callbacks may be registered; the first one sends every tag
    # collected so far and the rest find nothing left to send.
    transaction.on_commit(lambda: _flush(connection))
