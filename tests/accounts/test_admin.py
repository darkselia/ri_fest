import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_superuser_creates_user_through_admin(client, superuser, django_user_model):
    client.force_login(superuser)
    url = reverse("admin:accounts_user_add")
    response = client.get(url)
    assert response.status_code == 200
    assert "email" in response.context["adminform"].form.fields
    assert "username" not in response.context["adminform"].form.fields

    response = client.post(
        url,
        {
            "email": "  Created@Example.INVALID  ",
            "usable_password": "true",
            "password1": "synthetic-long-password-42",
            "password2": "synthetic-long-password-42",
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    created = django_user_model.objects.get(email="created@example.invalid")
    assert created.check_password("synthetic-long-password-42")
    assert not created.is_staff
    assert not created.is_superuser


def test_superuser_assigns_organizer_through_admin(client, superuser, member):
    client.force_login(superuser)
    group = Group.objects.get(name="Организаторы")
    response = client.post(
        reverse("admin:accounts_user_change", args=[member.pk]),
        {
            "email": member.email,
            "first_name": "",
            "last_name": "",
            "is_active": "on",
            "is_staff": "on",
            "groups": [group.pk],
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assert member.is_staff
    assert not member.is_superuser
    assert member.groups.filter(pk=group.pk).exists()


def test_admin_rejects_duplicate_email_without_server_error(
    client, superuser, member, organizer
):
    client.force_login(superuser)
    response = client.post(
        reverse("admin:accounts_user_change", args=[organizer.pk]),
        {"email": "MEMBER@EXAMPLE.INVALID", "is_active": "on"},
    )
    assert response.status_code == 200
    assert response.context["adminform"].form.errors
    organizer.refresh_from_db()
    assert organizer.email == "organizer@example.invalid"


def test_organizer_enters_admin_without_account_management(client, organizer):
    client.force_login(organizer)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert not response.context["available_apps"]


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "page",
    [
        "accounts_user_changelist",
        "accounts_user_add",
        "accounts_user_change",
        "accounts_user_delete",
        "auth_group_changelist",
        "auth_group_change",
    ],
)
def test_organizer_cannot_manage_accounts_or_groups(
    client, organizer, member, method, page
):
    client.force_login(organizer)
    args = []
    if page in {"accounts_user_change", "accounts_user_delete"}:
        args = [member.pk]
    elif page == "auth_group_change":
        args = [Group.objects.get(name="Организаторы").pk]
    response = getattr(client, method)(reverse(f"admin:{page}", args=args))
    assert response.status_code == 403
    member.refresh_from_db()
    assert not member.is_staff
    assert not member.is_superuser


def test_account_management_requires_superuser_even_with_model_permission(
    client, organizer, member
):
    from django.contrib.auth.models import Permission

    organizer.user_permissions.add(
        Permission.objects.get(
            codename="change_user", content_type__app_label="accounts"
        )
    )
    client.force_login(organizer)
    response = client.post(
        reverse("admin:accounts_user_change", args=[member.pk]),
        {"email": member.email, "is_staff": "on", "is_superuser": "on"},
    )
    assert response.status_code == 403
    member.refresh_from_db()
    assert not member.is_superuser
