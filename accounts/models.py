from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from .managers import UserManager


class User(AbstractUser):
    username = None
    email = models.EmailField("email", unique=True)
    email_verification_pending = models.BooleanField(
        "Ожидает подтверждения email", default=False, editable=False
    )
    email_verified_at = models.DateTimeField(
        "Email подтверждён", null=True, blank=True, editable=False
    )
    pending_email = models.EmailField(
        "Новый email ожидает подтверждения", blank=True, default="", editable=False
    )
    email_change_id = models.UUIDField(null=True, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        constraints = [
            models.UniqueConstraint(
                Lower("email"), name="accounts_user_email_ci_unique"
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email
