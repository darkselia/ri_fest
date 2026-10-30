import uuid

from django.conf import settings
from django.db import models


class FormField(models.Model):
    class Type(models.TextChoices):
        TEXT = "text", "Короткий текст"
        TEXTAREA = "textarea", "Многострочный текст"
        NUMBER = "number", "Число"
        DATE = "date", "Дата"
        BOOLEAN = "boolean", "Флажок"
        SINGLE = "single", "Один вариант"
        MULTI = "multi", "Несколько вариантов"
        INFO = "info", "Информационный блок"
        SERVICE = "service", "Платные услуги"

    festival = models.ForeignKey(
        "festivals.Festival",
        on_delete=models.PROTECT,
        related_name="form_fields",
        verbose_name="Фестиваль",
    )
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    field_type = models.CharField("Тип", max_length=8, choices=Type.choices)
    label = models.CharField("Название", max_length=255)
    help_text = models.TextField("Подсказка", blank=True)
    is_required = models.BooleanField("Обязательный", default=False)
    order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активный", default=True)

    class Meta:
        verbose_name = "вопрос анкеты"
        verbose_name_plural = "вопросы анкеты"
        ordering = ("order", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("festival", "key"),
                name="form_field_festival_key",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    field_type__in=[
                        "text",
                        "textarea",
                        "number",
                        "date",
                        "boolean",
                        "single",
                        "multi",
                        "info",
                        "service",
                    ]
                ),
                name="form_field_valid_type",
            ),
        ]

    def __str__(self):
        return self.label


class FormOption(models.Model):
    field = models.ForeignKey(
        FormField,
        on_delete=models.CASCADE,
        related_name="options",
        verbose_name="Вопрос",
    )
    label = models.CharField("Подпись", max_length=255)
    order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активный", default=True)

    class Meta:
        verbose_name = "вариант ответа"
        verbose_name_plural = "варианты ответа"
        ordering = ("order", "pk")

    def __str__(self):
        return self.label


class ServiceOption(models.Model):
    field = models.ForeignKey(
        FormField,
        on_delete=models.CASCADE,
        related_name="service_options",
        verbose_name="Ценовое поле",
    )
    label = models.CharField("Подпись", max_length=255)
    service_date = models.DateField("Дата", null=True, blank=True)
    price = models.DecimalField("Цена", max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField("Активный", default=True)
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "вариант услуги"
        verbose_name_plural = "варианты услуг"
        ordering = ("order", "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="service_price_nonnegative"
            ),
        ]

    def __str__(self):
        return self.label


class ApplicationQuerySet(models.QuerySet):
    def for_owner(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(owner=user)


class Application(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
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
        default=Status.DRAFT,
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
                condition=models.Q(status__in=["draft", "active", "withdrawn"]),
                name="application_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(owner__isnull=False) | ~models.Q(status="draft"),
                name="application_draft_has_owner",
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


class ApplicationServiceSelection(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="service_selections"
    )
    option = models.ForeignKey(
        ServiceOption, on_delete=models.PROTECT, related_name="selections"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("application", "option"), name="application_service_unique"
            )
        ]

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if self.application_id and self.option_id:
            if self.application.festival_id != self.option.field.festival_id:
                raise ValidationError("Услуга относится к другому фестивалю.")
