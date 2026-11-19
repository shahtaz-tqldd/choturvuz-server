from datetime import timedelta
from urllib.parse import urlparse

from celery.schedules import crontab

from app import constants
from app.settings.env import (
    PROJECT_DIR,
    env,
    env_bool,
    env_float,
    env_int,
    env_list,
)

APP_ENV = env("APP_ENV", "dev")
SECRET_KEY = env("APP_SECRET", "django-insecure-change-me")
DEBUG = APP_ENV == "dev"
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("SECURE_PROXY_SSL_HEADER_ENABLED", APP_ENV == "prod")
    else None
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "%(levelname)s %(asctime)s %(name)s %(filename)s:%(lineno)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "loggers": {
        "app": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=24),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# EMAILS
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_USE_TLS = True
EMAIL_PORT = 587
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "noreply@choturvuz.store"
)
EMAIL_VERIFICATION_OTP_TTL_MINUTES=10

# STATIC AND MEDIA
STATIC_URL = "/static/"
STATIC_ROOT = PROJECT_DIR / "staticfiles"
STATICFILES_DIRS = [PROJECT_DIR / "static"] if (PROJECT_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = PROJECT_DIR / "media"

# CELERY
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = env("TIME_ZONE", "UTC")
CELERY_RESULT_EXTENDED = True
CELERY_IMPORTS = (
    "base.tasks",
    "accounts.tasks",
    "orders.tasks",
    "app.tasks",
)
CELERY_BEAT_SCHEDULE = {
    "daily-store-maintenance": {
        "task": "base.tasks.run_daily_maintenance",
        "schedule": crontab(hour=0, minute=15),
    },
    # Stock is reserved when an order is placed, so unpaid bKash orders must
    # be expired promptly to give the stock back.
    "expire-unpaid-orders": {
        "task": "orders.tasks.expire_unpaid_orders",
        "schedule": crontab(minute="*/15"),
    },
}

# ORDERS
# Unpaid bKash orders (FULL/HALF) hold reserved stock for this long.
PENDING_PAYMENT_EXPIRY_HOURS = env_int(
    "PENDING_PAYMENT_EXPIRY_HOURS", constants.PENDING_PAYMENT_EXPIRY_HOURS
)

# CUSTOM ORDERS
# Max size of one design/logo file a customer uploads.
CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB = env_int(
    "CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB", constants.CUSTOM_ORDER_UPLOAD_MAX_SIZE_MB
)
# Max files attached to one custom order.
CUSTOM_ORDER_MAX_ATTACHMENTS = env_int(
    "CUSTOM_ORDER_MAX_ATTACHMENTS", constants.CUSTOM_ORDER_MAX_ATTACHMENTS
)
# Max pieces in one custom order line.
CUSTOM_ORDER_MAX_LINE_QUANTITY = env_int("CUSTOM_ORDER_MAX_LINE_QUANTITY", 1000)
# A quoted (bKash) custom order is cancelled when it stays unpaid this many
# hours after the quote was sent. 0 disables automatic expiry.
CUSTOM_ORDER_QUOTE_EXPIRY_HOURS = env_int(
    "CUSTOM_ORDER_QUOTE_EXPIRY_HOURS", constants.CUSTOM_ORDER_QUOTE_EXPIRY_HOURS
)
CUSTOM_ORDER_UPLOAD_EXTENSIONS = (
    "jpg", "jpeg", "png", "webp", "gif", "svg", "heic", "tif", "tiff",
    "pdf", "ai", "eps", "psd", "zip",
)

# CACHE — basic read-through caching for catalog data that rarely changes
# Redis-backed when CACHE_URL is set (for example redis://redis:6379/2);
# otherwise a per-process LocMemCache.
CACHE_URL = env("CACHE_URL", "")
CATALOG_CACHE_TTL_SECONDS = env_int("CATALOG_CACHE_TTL_SECONDS", 300)

if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "choturvuzstore",
        },
    }

# BKASH TOKENIZED CHECKOUT
BKASH_BASE_URL = env("BKASH_BASE_URL", "https://tokenized.sandbox.bka.sh/v1.2.0-beta")
BKASH_APP_KEY = env("BKASH_APP_KEY", "")
BKASH_APP_SECRET = env("BKASH_APP_SECRET", "")
BKASH_USERNAME = env("BKASH_USERNAME", "")
BKASH_PASSWORD = env("BKASH_PASSWORD", "")
BKASH_CALLBACK_URL = env("BKASH_CALLBACK_URL", "")
BKASH_TOKEN_CACHE_KEY = "bkash:tokenized_checkout:token"


# CLOUDINARY MEDIA STORAGE
# Either set the three CLOUDINARY_* credentials or a single
# CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>.
CLOUDINARY_URL = env("CLOUDINARY_URL", "")
_cloudinary_url = urlparse(CLOUDINARY_URL) if CLOUDINARY_URL else None
CLOUDINARY_CLOUD_NAME = env("CLOUDINARY_CLOUD_NAME", "") or (
    (_cloudinary_url.hostname or "") if _cloudinary_url else ""
)
CLOUDINARY_API_KEY = env("CLOUDINARY_API_KEY", "") or (
    (_cloudinary_url.username or "") if _cloudinary_url else ""
)
CLOUDINARY_API_SECRET = env("CLOUDINARY_API_SECRET", "") or (
    (_cloudinary_url.password or "") if _cloudinary_url else ""
)
# Every asset this app uploads lives under this folder.
CLOUDINARY_ROOT_FOLDER = (env("CLOUDINARY_ROOT_FOLDER", "choturvuz") or "choturvuz").strip("/")
CLOUDINARY_IMAGES_FOLDER = f"{CLOUDINARY_ROOT_FOLDER}/images"
CLOUDINARY_FILES_FOLDER = f"{CLOUDINARY_ROOT_FOLDER}/files"
CLOUDINARY_CUSTOM_ORDERS_FOLDER = f"{CLOUDINARY_ROOT_FOLDER}/custom-orders"
# Images are downscaled on upload so their longest side is at most this.
CLOUDINARY_IMAGE_MAX_DIMENSION = env_int("CLOUDINARY_IMAGE_MAX_DIMENSION", 2400)

# BASE APP FILE UPLOADS
BASE_FILE_MAX_SIZE_MB = env_int("BASE_FILE_MAX_SIZE_MB", 10)

# FRONTEND URL
# USER_FRONTEND_URL is the storefront (choturvuz-client); bKash sends customers
# back to {USER_FRONTEND_URL}/orders/track.
USER_FRONTEND_URL = env("USER_FRONTEND_URL", "http://localhost:3000")
ADMIN_FRONTEND_URL = env("ADMIN_FRONTEND_URL", "http://localhost:5174")

# STOREFRONT CACHE REVALIDATION (Next.js on-demand revalidation webhook)
# Disabled unless both values are set.
STOREFRONT_REVALIDATE_URL = env("STOREFRONT_REVALIDATE_URL", "")
STOREFRONT_REVALIDATE_SECRET = env("STOREFRONT_REVALIDATE_SECRET", "")

# LANGUAGE AND TIME
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True
