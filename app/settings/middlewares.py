from app import constants
from app.settings.env import env, env_int

BASE_MIDDLEWARES = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CUSTOM_MIDDLEWARES = []

MIDDLEWARE = BASE_MIDDLEWARES + CUSTOM_MIDDLEWARES

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "app.services.jwt.SafeJWTAuthentication",
    ),
    "EXCEPTION_HANDLER": "app.utils.exception_handler.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        # Guest checkout spam protection (POST /orders/).
        # The storefront calls the API from its server, so every customer
        # shares its IP unless it forwards X-Forwarded-For (with NUM_PROXIES
        # set). The per-IP limit is therefore opt-in.
        "order_create_ip": env("ORDER_CREATE_IP_RATE", constants.ORDER_CREATE_IP_RATE) or None,
        "order_create_phone": env("ORDER_CREATE_PHONE_RATE", constants.ORDER_CREATE_PHONE_RATE) or None,
        # Public design/logo uploads for custom orders, per client IP.
        "custom_upload_ip": env("CUSTOM_UPLOAD_IP_RATE", constants.CUSTOM_UPLOAD_IP_RATE) or None,
        # Public review submissions, per client IP.
        "review_create_ip": env("REVIEW_CREATE_IP_RATE", constants.REVIEW_CREATE_IP_RATE) or None,
    },
    "NUM_PROXIES": env_int("NUM_PROXIES", constants.NUM_PROXIES) or None,
}
