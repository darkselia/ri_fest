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
