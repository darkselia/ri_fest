from django.contrib.auth.views import (
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
)
from django.urls import path, reverse_lazy
from django.views.generic import TemplateView

from .forms import AccountSetPasswordForm
from .views import (
    AccountDeletionView,
    AccountHomeView,
    AccountLoginView,
    AccountPasswordChangeView,
    AccountPasswordResetView,
    ActivateEmailView,
    ActivationPendingView,
    EmailChangeConfirmView,
    EmailChangeView,
    PendingEmailChangeView,
    ProfileView,
    RegistrationView,
    ResendActivationView,
    skip_email_verification,
)

app_name = "accounts"

urlpatterns = [
    path("accounts/register/", RegistrationView.as_view(), name="register"),
    path(
        "accounts/activation/",
        ActivationPendingView.as_view(),
        name="activation_pending",
    ),
    path(
        "accounts/activate/<str:token>/", ActivateEmailView.as_view(), name="activate"
    ),
    path(
        "accounts/activation/resend/",
        ResendActivationView.as_view(),
        name="activation_resend",
    ),
    path(
        "accounts/activation/sent/",
        TemplateView.as_view(
            template_name="accounts/auth/status.html",
            extra_context={
                "title": "Проверьте почту",
                "description": "Если аккаунт ожидает подтверждения, на указанный адрес будет отправлено письмо.",
            },
        ),
        name="activation_sent",
    ),
    path("accounts/activation/skip/", skip_email_verification, name="activation_skip"),
    path(
        "accounts/password-reset/",
        AccountPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        PasswordResetDoneView.as_view(
            template_name="accounts/auth/status.html",
            extra_context={
                "description": "Если адрес связан с активным аккаунтом, на него будет отправлена инструкция для смены пароля.",
            },
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(
            form_class=AccountSetPasswordForm,
            template_name="accounts/auth/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        PasswordResetCompleteView.as_view(
            template_name="accounts/auth/status.html",
            extra_context={"description": "Пароль изменён. Войдите с новым паролем."},
        ),
        name="password_reset_complete",
    ),
    path("accounts/login/", AccountLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("account/", AccountHomeView.as_view(), name="home"),
    path("account/profile/", ProfileView.as_view(), name="profile"),
    path("account/delete/", AccountDeletionView.as_view(), name="delete"),
    path("account/email-change/", EmailChangeView.as_view(), name="email_change"),
    path(
        "account/email-change/resend/",
        PendingEmailChangeView.as_view(),
        {"action": "resend"},
        name="email_change_resend",
    ),
    path(
        "account/email-change/cancel/",
        PendingEmailChangeView.as_view(),
        {"action": "cancel"},
        name="email_change_cancel",
    ),
    path(
        "accounts/email-change/<str:token>/",
        EmailChangeConfirmView.as_view(),
        name="email_change_confirm",
    ),
    path(
        "account/password-change/",
        AccountPasswordChangeView.as_view(),
        name="password_change",
    ),
]
