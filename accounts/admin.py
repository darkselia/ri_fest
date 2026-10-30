from django.contrib import admin
from django.contrib.auth import logout
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm
from unfold.widgets import UnfoldAdminPasswordWidget

from .forms import AccountChangeForm, AccountCreationForm, AccountDeletionForm
from .models import User
from .rate_limits import rate_limit_response
from .services import AccountDeletionConflict, delete_account

admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    search_help_text = "Поиск по email, имени и фамилии"
    list_filter_sheet = False
    change_form_template = "admin/accounts/change_form.html"
    add_form = AccountCreationForm
    form = AccountChangeForm
    change_password_form = AdminPasswordChangeForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    readonly_fields = (
        "last_login",
        "date_joined",
        "email_verification_pending",
        "email_verified_at",
        "pending_email",
    )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Личные данные", {"fields": ("first_name", "last_name")}),
        (
            "Доступ",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Подтверждение email",
            {
                "fields": (
                    "email_verification_pending",
                    "email_verified_at",
                    "pending_email",
                )
            },
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {"fields": ("email", "usable_password", "password1", "password2")},
        ),
    )

    def save_model(self, request, obj, form, change):
        if change and not obj.is_active:
            # Saving an account as disabled is an explicit administrative block.
            obj.email_verification_pending = False
        if change and (not obj.is_active or "email" in form.changed_data):
            obj.pending_email = ""
            obj.email_change_id = None
        if change and "email" in form.changed_data:
            obj.email_verified_at = None
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        context["can_delete_account"] = (
            obj is not None
            and request.user.is_superuser
            and request.user.has_perm("accounts.delete_user")
        )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_urls(self):
        return [
            path(
                "<int:object_id>/delete-account/",
                self.admin_site.admin_view(self.delete_account_view),
                name="accounts_user_delete_account",
            )
        ] + super().get_urls()

    @method_decorator(sensitive_post_parameters("current_password"))
    def delete_account_view(self, request, object_id):
        if not request.user.is_superuser or not request.user.has_perm(
            "accounts.delete_user"
        ):
            raise PermissionDenied
        target = get_object_or_404(User, pk=object_id)
        form = AccountDeletionForm(
            request.POST if request.method == "POST" else None, user=request.user
        )
        form.fields["current_password"].widget = UnfoldAdminPasswordWidget()
        status = 200
        if request.method == "POST":
            limited = rate_limit_response(request, "login", email=request.user.email)
            if limited is not None:
                return limited
            if form.is_valid():
                try:
                    delete_account(
                        object_id,
                        actor=request.user,
                        password=form.cleaned_data["current_password"],
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                    status = 409 if isinstance(error, AccountDeletionConflict) else 200
                else:
                    if object_id == request.user.pk:
                        logout(request)
                        self.message_user(request, "Аккаунт удалён.")
                        return redirect("accounts:login")
                    self.message_user(
                        request, "Аккаунт удалён. Обезличенная история сохранена."
                    )
                    return redirect("admin:accounts_user_changelist")
        return TemplateResponse(
            request,
            "admin/festivals/confirm_action.html",
            {
                **self.admin_site.each_context(request),
                "title": "Удалить аккаунт",
                "description": (
                    "Аккаунт будет удалён без возможности восстановления. Все черновики будут удалены навсегда. Отправленные анкеты будут отозваны и обезличены. Для подтверждения введите свой пароль администратора."
                ),
                "confirm_label": "Удалить аккаунт",
                "danger": True,
                "form": form,
                "opts": self.model._meta,
                "original": target,
                "cancel_url": reverse("admin:accounts_user_change", args=[object_id]),
            },
            status=status,
        )
