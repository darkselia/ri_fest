from django.conf import settings
from django.db import models


class ApplicationQuerySet(models.QuerySet):
    def for_owner(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(owner=user)


class Application(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        WITHDRAWN = "withdrawn", "Отозвана"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Владелец",
        related_name="applications",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    festival = models.ForeignKey(
        "festivals.Festival",
        verbose_name="Фестиваль",
        related_name="applications",
        on_delete=models.PROTECT,
    )
    status = models.CharField(
        "Статус",
        max_length=9,
        choices=Status.choices,
        default=Status.ACTIVE,
        editable=False,
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Изменена", auto_now=True)
    anonymized_at = models.DateTimeField(
        "Обезличена", null=True, blank=True, editable=False
    )

    objects = ApplicationQuerySet.as_manager()

    class Meta:
        verbose_name = "анкета"
        verbose_name_plural = "анкеты"
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "withdrawn"]),
                name="application_valid_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(owner__isnull=False, anonymized_at__isnull=True)
                    | models.Q(owner__isnull=True, anonymized_at__isnull=False)
                ),
                name="application_owner_anonymized",
            ),
        ]

    def __str__(self):
        return f"Анкета №{self.pk}" if self.pk else "Новая анкета"
