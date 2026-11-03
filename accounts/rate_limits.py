import hashlib

from django.conf import settings
from django.core.cache import caches
from django.template.response import TemplateResponse
from redis.exceptions import RedisError

from .models import User


def rate_limit_response(request, action, *, email=None):
    email = User.objects.normalize_email(
        email
        if email is not None
        else request.POST.get("email", request.POST.get("username", ""))
    )
    ip = request.META.get("REMOTE_ADDR", "")
    identities = {"ip": ip, "email": email, "email_ip": f"{email}:{ip}"}
    cache = caches["auth"]
    try:
        for scope, (maximum, seconds) in settings.AUTH_RATE_LIMITS[action].items():
            digest = hashlib.sha256(identities[scope].encode()).hexdigest()
            key = f"{action}:{scope}:{digest}"
            if cache.add(key, 1, timeout=seconds):
                count = 1
            else:
                try:
                    count = cache.incr(key)
                except ValueError:
                    # A key may expire between add() and incr(). Never reset a live key.
                    if cache.add(key, 1, timeout=seconds):
                        count = 1
                    else:
                        count = cache.incr(key)
            if count > maximum:
                response = TemplateResponse(
                    request,
                    "accounts/auth/status.html",
                    {
                        "title": "Слишком много попыток",
                        "description": "Подождите немного и повторите запрос.",
                    },
                    status=429,
                )
                response["Retry-After"] = str(seconds)
                return response
    except (RedisError, OSError):
        return TemplateResponse(
            request,
            "accounts/auth/status.html",
            {
                "title": "Сервис временно недоступен",
                "description": "Не удалось обработать запрос. Повторите его позже.",
            },
            status=503,
        )
    return None


class RateLimitedMixin:
    rate_limit_action = "mail"

    def post(self, request, *args, **kwargs):
        response = rate_limit_response(request, self.rate_limit_action)
        if response is not None:
            return response
        return super().post(request, *args, **kwargs)
