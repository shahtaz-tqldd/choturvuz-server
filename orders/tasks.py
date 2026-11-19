from celery import shared_task


@shared_task(name="orders.tasks.expire_unpaid_orders")
def expire_unpaid_orders():
    from orders.services.maintenance import cancel_stale_pending_orders

    return {"cancelled": cancel_stale_pending_orders()}
