DJANGO_BASE_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django.contrib.postgres",
    "rest_framework",
    "corsheaders",
    "django_celery_results",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
]


MODULER_APPS = [
    "base.apps.BaseConfig",
    "accounts.apps.AccountsConfig",
    "catalog.apps.CatalogConfig",
    "orders.apps.OrdersConfig",
    "reviews.apps.ReviewsConfig",
]

INSTALLED_APPS = DJANGO_BASE_APPS + THIRD_PARTY_APPS + MODULER_APPS
