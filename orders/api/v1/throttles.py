from rest_framework.throttling import SimpleRateThrottle

from app.utils.phone import normalize_phone


class OrderCreateIPThrottle(SimpleRateThrottle):
    scope = "order_create_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class OrderCreatePhoneThrottle(SimpleRateThrottle):
    """Limit orders per customer phone, whatever IP they come from."""

    scope = "order_create_phone"

    def get_cache_key(self, request, view):
        data = request.data if hasattr(request.data, "get") else {}
        phone = normalize_phone(data.get("customer_phone", ""))
        if not phone:
            return None
        return self.cache_format % {"scope": self.scope, "ident": phone}


class CustomUploadIPThrottle(SimpleRateThrottle):
    """Limit public design/logo uploads per client IP."""

    scope = "custom_upload_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
