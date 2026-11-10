from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView

from .forms import (
    AccountDeletionForm,
    AccountPasswordChangeForm,
    AccountPasswordResetForm,
    EmailAuthenticationForm,
    EmailChangeForm,
    EmailForm,
    ProfileForm,
    RegistrationForm,
)
from .models import User
from .rate_limits import RateLimitedMixin, rate_limit_response
from .services import (
    PENDING_SESSION_KEY,
    AccountDeletionConflict,
    cancel_email_change,
    change_email,
    confirm_email,
    confirm_email_change,
    delete_account,
    email_change_user,
    send_email_change_confirmation,
    send_verification_email,
    verification_user,
)


class AccountLoginView(RateLimitedMixin, LoginView):
    rate_limit_action = "login"
    template_name = "accounts/auth/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        destination = self.get_redirect_url()
        if self.request.user.is_staff and urlsplit(destination).path.startswith(
            reverse("admin:index")
        ):
            return destination
        return reverse("accounts:home")


@method_decorator(never_cache, name="dispatch")
@method_decorator(sensitive_post_parameters("password1", "password2"), name="dispatch")
class RegistrationView(RateLimitedMixin, FormView):
    rate_limit_action = "register"
    form_class = RegistrationForm
    template_name = "accounts/auth/form.html"
    extra_context = {
        "title": "Регистрация",
        "description": "Укажите email и придумайте пароль.",
        "submit_label": "Зарегистрироваться",
    }

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.is_staff = False
                user.is_superuser = False
                user.is_active = not settings.EMAIL_VERIFICATION_REQUIRED
                user.email_verification_pending = True
                user.save()

                def send_confirmation():
                    if not send_verification_email(user, self.request):
                        messages.error(
                            self.request,
                            "Не удалось отправить письмо подтверждения. "
                            "Попробуйте отправить его повторно позже.",
                        )

                transaction.on_commit(send_confirmation)
        except IntegrityError:
            if not User.objects.filter(
                email__iexact=form.cleaned_data["email"]
            ).exists():
                raise
            form.add_error("email", "Аккаунт с этим email уже существует.")
            return self.form_invalid(form)
        if settings.EMAIL_VERIFICATION_REQUIRED:
            self.request.session[PENDING_SESSION_KEY] = user.pk
            return redirect("accounts:activation_pending")
        self.request.session.pop(PENDING_SESSION_KEY, None)
        login(self.request, user)
        messages.success(
            self.request,
            "Регистрация завершена. Для подтверждения email перейдите по ссылке "
            "из письма. Пока вы можете пользоваться личным кабинетом без подтверждения.",
        )
        return redirect("accounts:home")


@method_decorator(never_cache, name="dispatch")
class ActivationPendingView(TemplateView):
    template_name = "accounts/auth/activation_pending.html"

    def get(self, request, *args, **kwargs):
        user = User.objects.filter(
            pk=request.session.get(PENDING_SESSION_KEY), email_verification_pending=True
        ).first()
        if user is None:
            return redirect("accounts:login")
        return self.render_to_response(
            {
                "title": "Подтвердите email",
                "pending_email": user.email,
                "can_skip": not settings.EMAIL_VERIFICATION_REQUIRED,
            }
        )


@method_decorator(never_cache, name="dispatch")
class ActivateEmailView(View):
    def get(self, request, token):
        return TemplateResponse(
            request,
            "accounts/auth/activate.html",
            {
                "title": "Подтверждение email",
                "valid_link": verification_user(token) is not None,
            },
        )

    def post(self, request, token):
        user = confirm_email(token)
        if user is None:
            return TemplateResponse(
                request,
                "accounts/auth/activate.html",
                {"title": "Ссылка недействительна", "valid_link": False},
                status=400,
            )
        request.session.pop(PENDING_SESSION_KEY, None)
        login(request, user)
        messages.success(request, "Email подтверждён.")
        return redirect("accounts:profile")


@require_POST
def skip_email_verification(request):
    if settings.EMAIL_VERIFICATION_REQUIRED:
        raise Http404
    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(
                pk=request.session.get(PENDING_SESSION_KEY),
                email_verification_pending=True,
                is_staff=False,
                is_superuser=False,
            )
            .first()
        )
        if user is None or (
            request.user.is_authenticated and request.user.pk != user.pk
        ):
            raise PermissionDenied
        user.is_active = True
        user.save(update_fields=["is_active"])
    request.session.pop(PENDING_SESSION_KEY, None)
    login(request, user)
    return redirect("accounts:home")


class ResendActivationView(RateLimitedMixin, FormView):
    template_name = "accounts/auth/form.html"
    form_class = EmailForm
    extra_context = {
        "title": "Повторить письмо подтверждения",
        "description": "Укажите email, использованный при регистрации.",
        "submit_label": "Отправить письмо",
    }

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:profile")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().post(request, *args, **kwargs)
        response = rate_limit_response(request, "mail", email=request.user.email)
        if response is not None:
            return response
        if request.user.email_verified_at is None:
            if send_verification_email(request.user, request):
                messages.success(
                    request,
                    "Письмо подтверждения отправлено. Перейдите по ссылке в письме. "
                    "Если его нет, проверьте папку «Спам».",
                )
            else:
                messages.error(
                    request,
                    "Не удалось отправить письмо подтверждения. Попробуйте позже.",
                )
        return redirect("accounts:profile")

    def form_valid(self, form):
        user = (
            User.objects.filter(
                email=form.cleaned_data["email"], email_verified_at__isnull=True
            )
            .filter(Q(is_active=True) | Q(email_verification_pending=True))
            .first()
        )
        if user is not None:
            send_verification_email(user, self.request)
        return redirect("accounts:activation_sent")


class AccountPasswordResetView(RateLimitedMixin, PasswordResetView):
    form_class = AccountPasswordResetForm
    template_name = "accounts/auth/form.html"
    email_template_name = "accounts/emails/password_reset.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_context = {
        "description": "Укажите email аккаунта, чтобы получить ссылку для смены пароля.",
        "submit_label": "Восстановить пароль",
    }


@method_decorator(never_cache, name="dispatch")
class AccountHomeView(LoginRequiredMixin, TemplateView):
    template_name = "applications/list.html"
    extra_context = {"account_tab": "applications"}

    def get_context_data(self, **kwargs):
        from applications.views import dashboard_context

        return {
            **super().get_context_data(**kwargs),
            **dashboard_context(self.request.user, self.request.GET),
        }


@method_decorator(never_cache, name="dispatch")
class ProfileView(LoginRequiredMixin, FormView):
    form_class = ProfileForm
    template_name = "accounts/settings/profile.html"
    extra_context = {
        "title": "Личные данные",
        "submit_label": "Сохранить изменения",
        "account_tab": "profile",
    }

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "instance": self.request.user}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["email_change_form"] = EmailChangeForm(user=self.request.user)
        context["password_form"] = AccountPasswordChangeForm(user=self.request.user)
        context["email_verification_required"] = settings.EMAIL_VERIFICATION_REQUIRED
        return context

    def form_valid(self, form):
        user = form.save(commit=False)
        user.save(update_fields=["first_name", "last_name"])
        messages.success(self.request, "Личные данные сохранены.")
        return redirect("accounts:profile")


@method_decorator(sensitive_post_parameters("current_password"), name="dispatch")
class EmailChangeView(ProfileView):
    form_class = EmailChangeForm

    def get(self, request, *args, **kwargs):
        return redirect("accounts:profile")

    def get_form_kwargs(self):
        return {**FormView.get_form_kwargs(self), "user": self.request.user}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["email_change_form"] = context["form"]
        context["form"] = ProfileForm(instance=self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        response = rate_limit_response(request, "login", email=request.user.email)
        if response is not None:
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        new_email = form.cleaned_data["new_email"]
        if new_email != self.request.user.email:
            response = rate_limit_response(self.request, "mail", email=new_email)
            if response is not None:
                return response
        try:
            user, result = change_email(
                self.request.user.pk, new_email, form.cleaned_data["current_password"]
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)
        if result == "unchanged":
            messages.info(self.request, "Этот email уже используется для входа.")
        else:
            if result == "pending":
                messages.info(
                    self.request,
                    "Новый email станет адресом для входа после подтверждения. "
                    f"Пока используйте прежний адрес: {user.email}.",
                )
            else:
                messages.success(
                    self.request,
                    f"Email изменён. Теперь для входа используйте {user.email}. "
                    "Подтверждение адреса необязательно.",
                )

            def send_confirmation():
                send = (
                    send_email_change_confirmation
                    if result == "pending"
                    else send_verification_email
                )
                if not send(user, self.request):
                    messages.error(
                        self.request,
                        "Не удалось отправить письмо подтверждения. Попробуйте позже.",
                    )

            transaction.on_commit(send_confirmation)
        return redirect("accounts:profile")


class PendingEmailChangeView(LoginRequiredMixin, View):
    def post(self, request, action):
        if action == "cancel":
            if cancel_email_change(request.user.pk):
                messages.success(request, "Изменение email отменено.")
            else:
                messages.info(request, "Ожидающей смены email нет.")
        else:
            user = User.objects.get(pk=request.user.pk)
            if not user.is_active:
                raise PermissionDenied
            if user.pending_email and user.email_change_id:
                response = rate_limit_response(
                    request, "mail", email=user.pending_email
                )
                if response is not None:
                    return response
                if send_email_change_confirmation(user, request):
                    messages.success(
                        request, "Письмо для нового email отправлено повторно."
                    )
                else:
                    messages.error(
                        request, "Не удалось отправить письмо. Попробуйте позже."
                    )
        return redirect("accounts:profile")


@method_decorator(never_cache, name="dispatch")
class EmailChangeConfirmView(View):
    template_name = "accounts/auth/email_change_confirm.html"

    def get(self, request, token):
        user = email_change_user(token)
        return TemplateResponse(
            request,
            self.template_name,
            {
                "title": "Подтверждение нового email",
                "valid_link": user is not None,
                "new_email": user.pending_email if user else "",
            },
        )

    def post(self, request, token):
        try:
            user = confirm_email_change(token)
        except ValidationError:
            return TemplateResponse(
                request,
                self.template_name,
                {
                    "title": "Email уже занят",
                    "valid_link": False,
                    "error_message": "Этот email уже занят. Укажите другой адрес в личном кабинете.",
                },
                status=409,
            )
        if user is None:
            return TemplateResponse(
                request,
                self.template_name,
                {"title": "Ссылка недействительна", "valid_link": False},
                status=400,
            )
        if request.user.is_authenticated and request.user.pk == user.pk:
            messages.success(request, "Email подтверждён и изменён.")
            return redirect("accounts:profile")
        return TemplateResponse(
            request,
            self.template_name,
            {"title": "Email изменён", "changed": True, "new_email": user.email},
        )


@method_decorator(never_cache, name="dispatch")
@method_decorator(sensitive_post_parameters("current_password"), name="dispatch")
class AccountDeletionView(LoginRequiredMixin, FormView):
    template_name = "accounts/settings/delete.html"
    form_class = AccountDeletionForm
    extra_context = {"title": "Удаление аккаунта", "account_tab": "profile"}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def post(self, request, *args, **kwargs):
        response = rate_limit_response(request, "login", email=request.user.email)
        if response is not None:
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            delete_account(
                self.request.user.pk,
                actor=self.request.user,
                password=form.cleaned_data["current_password"],
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.render_to_response(
                self.get_context_data(form=form),
                status=409 if isinstance(error, AccountDeletionConflict) else 200,
            )
        logout(self.request)
        messages.success(self.request, "Аккаунт удалён.")
        return redirect("accounts:login")


class AccountPasswordChangeView(PasswordChangeView):
    form_class = AccountPasswordChangeForm
    template_name = "accounts/settings/profile.html"
    success_url = reverse_lazy("accounts:profile")
    extra_context = {
        "title": "Изменить пароль",
        "submit_label": "Изменить пароль",
        "account_tab": "password",
    }

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        current = (
            User.objects.select_for_update()
            .filter(pk=request.user.pk, is_active=True)
            .first()
        )
        if current is None:
            raise PermissionDenied
        request.user = current
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["password_form"] = context["form"]
        context["form"] = ProfileForm(instance=self.request.user)
        context["email_change_form"] = EmailChangeForm(user=self.request.user)
        context["email_verification_required"] = settings.EMAIL_VERIFICATION_REQUIRED
        context["account_tab"] = "profile"
        context["title"] = "Личные данные"
        context["submit_label"] = "Сохранить изменения"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Пароль изменён.")
        return response
