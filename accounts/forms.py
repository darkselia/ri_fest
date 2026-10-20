from django import forms
from django.contrib.auth.forms import (
    AdminUserCreationForm,
    AuthenticationForm,
    UserChangeForm,
)

from .models import User


class AccountCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {}


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"
        field_classes = {}


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"autofocus": True, "autocomplete": "username"},
        ),
    )
