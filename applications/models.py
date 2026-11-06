import uuid

from django.conf import settings
from django.db import models, transaction


class ApplicationNumberSequence(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    festival = models.ForeignKey("festivals.Festival", on_delete=models.CASCADE)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "festival"), name="application_number_sequence_unique"
            )
        ]


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
        SERVICE = "service", "Платные услуги — несколько вариантов"
        SERVICE_SINGLE = "service1", "Платные услуги — один вариант"
        DATE_FEE = "datefee", "Плата по дате"

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
                        "service1",
                        "datefee",
                    ]
                ),
                name="form_field_valid_type",
            ),
        ]

    def __str__(self):
        return self.label


class FeePeriod(models.Model):
    field = models.ForeignKey(
        FormField, on_delete=models.CASCADE, related_name="fee_periods"
    )
    ends_on = models.DateField("До включительно", null=True, blank=True)
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)

    class Meta:
        ordering = (models.F("ends_on").asc(nulls_last=True), "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="fee_period_nonnegative"
            ),
            models.UniqueConstraint(
                fields=("field",),
                condition=models.Q(ends_on__isnull=True),
                name="fee_period_one_open",
            ),
            models.UniqueConstraint(
                fields=("field", "ends_on"), name="fee_period_unique_end"
            ),
        ]


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
    is_opt_out = models.BooleanField("Отказ от услуги", default=False)
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
            models.CheckConstraint(
                condition=models.Q(is_opt_out=False)
                | models.Q(price=0, service_date__isnull=True),
                name="service_refusal_free_undated",
            ),
            models.UniqueConstraint(
                fields=("field",),
                condition=models.Q(is_opt_out=True),
                name="service_one_refusal",
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
        ARCHIVED = "archived", "В архиве"

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
    number = models.PositiveIntegerField(
        "Номер анкеты владельца на фестиваль", null=True, editable=False
    )
    updated_at = models.DateTimeField("Изменена", auto_now=True)
    paid_amount = models.DecimalField(
        "Внесено", max_digits=12, decimal_places=2, default=0, editable=False
    )
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
                condition=models.Q(paid_amount__gte=0),
                name="application_paid_amount_nonnegative",
            ),
            models.UniqueConstraint(
                fields=("owner", "festival", "number"),
                name="application_owner_festival_number_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(number__isnull=True) | models.Q(number__gt=0),
                name="application_number_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "active", "withdrawn", "archived"]
                ),
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
        return f"Анкета {self.number}" if self.number else "Новая анкета"

    def save(self, *args, **kwargs):
        if self.owner_id is None or self.number is not None:
            return super().save(*args, **kwargs)
        from django.contrib.auth import get_user_model

        from festivals.models import Festival

        with transaction.atomic():
            Festival.objects.select_for_update().get(pk=self.festival_id)
            get_user_model().objects.select_for_update().get(pk=self.owner_id)
            sequence, _ = ApplicationNumberSequence.objects.get_or_create(
                owner_id=self.owner_id, festival_id=self.festival_id
            )
            sequence = ApplicationNumberSequence.objects.select_for_update().get(
                pk=sequence.pk
            )
            self.number = sequence.next_number
            sequence.next_number += 1
            sequence.save(update_fields=["next_number"])
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "number"}
            return super().save(*args, **kwargs)


class ApplicationAnswer(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="answers"
    )
    field = models.ForeignKey(
        FormField, on_delete=models.PROTECT, related_name="answers"
    )
    value = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("application", "field"), name="application_answer_unique"
            )
        ]

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if self.application_id and self.field_id:
            if self.application.festival_id != self.field.festival_id:
                raise ValidationError("Вопрос относится к другому фестивалю.")
            if self.field.field_type in (
                FormField.Type.INFO,
                FormField.Type.DATE_FEE,
                FormField.Type.SERVICE,
                FormField.Type.SERVICE_SINGLE,
            ):
                raise ValidationError("Этот элемент не хранит обычный ответ.")


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
