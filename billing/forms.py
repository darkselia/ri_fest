from django import forms
from unfold.widgets import UnfoldAdminTextInputWidget


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        label="Сумма",
        min_value=0,
        max_digits=12,
        decimal_places=2,
        localize=True,
        widget=UnfoldAdminTextInputWidget,
    )


class FeeLockForm(forms.Form):
    field_id = forms.IntegerField(min_value=1)
    fee_action = forms.ChoiceField(
        choices=[(a, a) for a in ("lock", "replace", "unlock")]
    )
    period_id = forms.IntegerField(min_value=1, required=False)
    fee_version = forms.CharField()

    def clean(self):
        data = super().clean()
        if data.get("fee_action") != "unlock" and not data.get("period_id"):
            self.add_error("period_id", "Выберите тариф блока.")
        if data.get("fee_action") == "unlock":
            data["period_id"] = None
        return data
