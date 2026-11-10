from django.conf import settings
from django.db import models


class FeeLock(models.Model):
    application = models.ForeignKey(
        "applications.Application", on_delete=models.CASCADE, related_name="fee_locks"
    )
    field = models.ForeignKey(
        "applications.FormField", on_delete=models.CASCADE, related_name="fee_locks"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    locked_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("application", "field"),
                name="fee_lock_application_field_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="fee_lock_amount_nonnegative"
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.application_id and self.field_id:
            if (
                self.field.festival_id != self.application.festival_id
                or self.field.field_type != "datefee"
                or not self.field.is_active
            ):
                raise ValidationError(
                    "Фиксация требует активного блока платы фестиваля анкеты."
                )


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        SET = "set", "Задать сумму"
        ADD = "add", "Прибавить"
        SUBTRACT = "subtract", "Вычесть"

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        related_name="payment_audit",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    action = models.CharField(max_length=8, choices=Action.choices)
    before = models.DecimalField(max_digits=12, decimal_places=2)
    after = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pk",)
