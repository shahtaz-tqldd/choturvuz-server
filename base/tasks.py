from celery import shared_task
from django.utils import timezone

from accounts.tasks import delete_expired_accounts


@shared_task(name="base.tasks.run_daily_maintenance")
def run_daily_maintenance():
    """Single daily coordinator for store maintenance."""
    from orders.services.maintenance import cancel_stale_pending_orders

    now = timezone.now()
    return {
        "run_at": now.isoformat(),
        "daily": {
            "expired_accounts_deleted": delete_expired_accounts(),
            "stale_pending_orders_cancelled": cancel_stale_pending_orders(now=now),
        },
    }
