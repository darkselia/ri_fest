from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .forms import AccountChangeForm, AccountCreationForm
from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = AccountCreationForm
    form = AccountChangeForm
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
        return request.user.is_superuser
