"""bKash Tokenized Checkout integration.

Flow (sandbox docs: https://developer.bka.sh):
    1. grant_token     -> id_token used as a bearer token
    2. create_payment  -> paymentID + bkashURL (redirect the customer)
    3. execute_payment -> after the customer is redirected back with
                          status=success, execute to capture the money

Credentials come from the BKASH_* environment variables.
"""

import requests
from django.core.cache import cache
from django.conf import settings


class BkashError(Exception):
    """Raised for any bKash API failure; message is safe to show clients."""


def is_configured():
    return bool(
        settings.BKASH_APP_KEY
        and settings.BKASH_APP_SECRET
        and settings.BKASH_USERNAME
        and settings.BKASH_PASSWORD
    )


def _request(method, path, *, params=None, json=None, token=None):
    url = f"{settings.BKASH_BASE_URL.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
        headers["X-App-Key"] = settings.BKASH_APP_KEY

    try:
        response = requests.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise BkashError("Could not reach bKash. Please try again.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise BkashError(
            f"Unexpected response from bKash (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or str(payload.get("statuscode", "")).startswith(
        "4"
    ):
        raise BkashError(
            payload.get("statusMessage")
            or payload.get("errorMessage")
            or f"bKash request failed (HTTP {response.status_code})."
        )
    return payload


def grant_token():
    if not is_configured():
        raise BkashError("bKash payment is not configured.")

    payload = _request(
        "POST",
        "/tokenized/checkout/token/grant",
        json={
            "app_key": settings.BKASH_APP_KEY,
            "app_secret": settings.BKASH_APP_SECRET,
        },
    )
    token = payload.get("id_token")
    if not token:
        raise BkashError("bKash did not return a token.")
    return token


def get_token():
    """Reuse a cached token until close to its refresh window."""
    token = cache.get(settings.BKASH_TOKEN_CACHE_KEY)
    if token:
        return token
    token = grant_token()
    # id_token typically lives 1h; refresh safely after 50 minutes.
    cache.set(settings.BKASH_TOKEN_CACHE_KEY, token, 50 * 60)
    return token


def refresh_token(refresh_token_value):
    payload = _request(
        "POST",
        "/tokenized/checkout/token/refresh",
        json={
            "app_key": settings.BKASH_APP_KEY,
            "refresh_token": refresh_token_value,
        },
    )
    return payload.get("id_token")


def create_payment(*, amount, invoice_number, callback_url, customer_phone=""):
    """Create a tokenized checkout payment; returns paymentID + redirect URL."""
    if not is_configured():
        raise BkashError("bKash payment is not configured.")
    if not callback_url:
        raise BkashError("bKash callback URL is not configured.")

    token = get_token()
    payload = _request(
        "POST",
        "/tokenized/checkout/create",
        json={
            "mode": "0011",
            "payerReference": customer_phone or "guest",
            "callbackURL": callback_url,
            "amount": str(amount),
            "currency": "BDT",
            "intent": "sale",
            "merchantInvoiceNumber": invoice_number,
        },
        token=token,
    )

    payment_id = payload.get("paymentID")
    bkash_url = payload.get("bkashURL")
    if not payment_id or not bkash_url:
        raise BkashError(payload.get("statusMessage") or "bKash payment creation failed.")
    return payload


def execute_payment(payment_id):
    """Capture a created payment after the customer approved it."""
    token = get_token()
    return _request(
        "POST",
        "/tokenized/checkout/execute",
        json={"paymentID": payment_id},
        token=token,
    )


def query_payment(payment_id):
    token = get_token()
    return _request(
        "POST",
        "/tokenized/checkout/payment/status",
        json={"paymentID": payment_id},
        token=token,
    )


def refund_payment(*, payment_id, trx_id, amount, reason="", sku=""):
    """Refund (part of) a completed payment. Returns the bKash payload,
    including `refundTrxID` on success."""
    if not is_configured():
        raise BkashError("bKash payment is not configured.")
    token = get_token()
    payload = _request(
        "POST",
        "/tokenized/checkout/payment/refund",
        json={
            "paymentID": payment_id,
            "trxID": trx_id,
            "amount": str(amount),
            "reason": reason or "Refund",
            "sku": sku or "order",
        },
        token=token,
    )
    if str(payload.get("transactionStatus", "")).lower() != "completed":
        raise BkashError(
            payload.get("statusMessage")
            or payload.get("errorMessage")
            or "bKash did not complete the refund."
        )
    return payload
