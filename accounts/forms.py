from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    BaseUserCreationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.template.loader import render_to_string
from unfold.forms import UserChangeForm, UserCreationForm

from .models import User
from .services import send_account_email


class RegistrationForm(BaseUserCreationForm):
    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {}
        widgets = {
            "email": forms.EmailInput(
                attrs={"autocomplete": "username", "inputmode": "email"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password2"].help_text = self.fields["password1"].help_text
        self.fields["password1"].help_text = ""

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Аккаунт с этим email уже существует.")
        return email


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"})
    )

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data["email"])


class AccountPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            autocomplete="email", inputmode="email"
        )

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data["email"])

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        send_account_email(
            subject, render_to_string(email_template_name, context), to_email
        )


class AccountSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password2"].help_text = self.fields["new_password1"].help_text
        self.fields["new_password1"].help_text = ""


class AccountPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password2"].help_text = self.fields["new_password1"].help_text
        self.fields["new_password1"].help_text = ""


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name")
        labels = {"first_name": "Имя", "last_name": "Фамилия"}
        widgets = {
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
        }


class EmailChangeForm(forms.Form):
    new_email = forms.EmailField(
        label="Новый email",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "inputmode": "email"}),
    )
    current_password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.initial.setdefault("new_email", user.pending_email or user.email)

    def clean_new_email(self):
        return User.objects.normalize_email(self.cleaned_data["new_email"])

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Текущий пароль неверен.")
        return password


class AccountDeletionForm(forms.Form):
    current_password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Текущий пароль неверен.")
        return password


class AccountCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[
            "usable_password"
        ].help_text = "Разрешите вход по паролю или создайте аккаунт без пароля."

    class Meta(UserCreationForm.Meta):
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
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "inputmode": "email",
            },
        ),
    )
