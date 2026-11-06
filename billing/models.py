from django.conf import settings
from django.db import models


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
