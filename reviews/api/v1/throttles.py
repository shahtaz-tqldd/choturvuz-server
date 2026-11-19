from rest_framework.throttling import SimpleRateThrottle


class ReviewCreateIPThrottle(SimpleRateThrottle):
    scope = "review_create_ip"

    def get_cache_key(self, request, view):
        if request.method != "POST":
            return None
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
