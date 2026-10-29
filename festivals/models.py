from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Укажите существующий часовой пояс.") from None


class Festival(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        OPEN = "open", "Принимает заявки"
        RUNNING = "running", "Проходит"
        ARCHIVED = "archived", "Архивирован"

    name = models.CharField("Название", max_length=255)
    theme = models.CharField("Тема", max_length=255, blank=True)
    start_date = models.DateField("Дата начала", blank=True, null=True)
    end_date = models.DateField("Дата окончания", blank=True, null=True)
    timezone = models.CharField(
        "Часовой пояс",
        max_length=64,
        default="Asia/Krasnoyarsk",
        validators=[validate_timezone],
        help_text="Например: Asia/Krasnoyarsk",
    )
    status = models.CharField(
        "Статус",
        max_length=8,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    published_at = models.DateTimeField(
        "Первая публикация",
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = "фестиваль"
        verbose_name_plural = "фестивали"
        ordering = ("-start_date", "-pk")
        permissions = [
            ("publish_festival", "Может публиковать фестиваль"),
            ("restore_festival", "Может возвращать фестиваль из архива"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["draft", "open", "running", "archived"]),
                name="festival_valid_status",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="festival_ordered_dates",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="draft")
                    | (
                        Q(status__in=["open", "running", "archived"])
                        & Q(published_at__isnull=False)
                        & Q(start_date__isnull=False, end_date__isnull=False)
                    )
                ),
                name="festival_publication_complete",
            ),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Окончание раньше начала фестиваля."})

    def __str__(self):
        return self.name
