import requests
from celery import shared_task
from django.conf import settings

from app.utils.logger import logger


@shared_task(
    name="app.tasks.send_storefront_revalidation",
    autoretry_for=(requests.RequestException,),
    retry_backoff=True,
    max_retries=5,
)
def send_storefront_revalidation(tags):
    url = settings.STOREFRONT_REVALIDATE_URL
    secret = settings.STOREFRONT_REVALIDATE_SECRET
    if not url or not secret or not tags:
        return {"sent": False}

    response = requests.post(
        url,
        json={"tags": list(tags)},
        headers={"x-revalidate-secret": secret},
        timeout=10,
    )
    if response.status_code >= 500:
        # Retry server errors; 4xx means a config problem, so just log it.
        raise requests.RequestException(
            f"Storefront revalidation failed with HTTP {response.status_code}."
        )
    if response.status_code >= 400:
        logger.warning(
            "Storefront revalidation rejected (HTTP %s) for tags %s",
            response.status_code,
            tags,
        )
    return {"sent": True, "status": response.status_code, "tags": list(tags)}
