import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def login_data(email="member@example.invalid"):
    return {"username": email, "password": "synthetic-account-password-42"}


def test_shared_login_form(client):
    response = client.get(reverse("accounts:login"))
    assert response.status_code == 200
    assert "accounts/login.html" in {template.name for template in response.templates}
    assert 'type="email"' in response.text
    assert 'name="csrfmiddlewaretoken"' in response.text
    assert "css/accounts.css" in response.text


@pytest.mark.parametrize(
    ("fixture_name", "destination"),
    [
        ("member", "accounts:home"),
        ("organizer", "accounts:home"),
        ("superuser", "accounts:home"),
    ],
)
def test_normal_login_opens_account_for_every_role(
    client, request, fixture_name, destination
):
    user = request.getfixturevalue(fixture_name)
    response = client.post(reverse("accounts:login"), login_data(user.email))
    assert response.status_code == 302
    assert response.url == reverse(destination)
    assert int(client.session["_auth_user_id"]) == user.pk


def test_shared_login_normalizes_email(client, member):
    response = client.post(
        reverse("accounts:login"), login_data(" MEMBER@EXAMPLE.INVALID ")
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:home")


def test_login_error_preserves_email_and_does_not_echo_password(client, member):
    response = client.post(
        reverse("accounts:login"),
        {"username": "Member@Example.INVALID", "password": "wrong-synthetic-password"},
    )
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert response.context["form"]["username"].value() == "Member@Example.INVALID"
    assert 'value="Member@Example.INVALID"' in response.text
    assert "wrong-synthetic-password" not in response.text
    assert "_auth_user_id" not in client.session


def test_inactive_account_cannot_log_in(client, member):
    member.is_active = False
    member.save(update_fields=["is_active"])
    response = client.post(reverse("accounts:login"), login_data())
    assert response.status_code == 200
    assert response.context["form"].errors
    assert "_auth_user_id" not in client.session


def test_login_ignores_privilege_fields_from_browser(client, member):
    group = Group.objects.get(name="Организаторы")
    response = client.post(
        reverse("accounts:login"),
        {
            **login_data(),
            "is_staff": "on",
            "is_superuser": "on",
            "groups": [group.pk],
        },
    )
    assert response.url == reverse("accounts:home")
    member.refresh_from_db()
    assert not member.is_staff
    assert not member.is_superuser
    assert not member.groups.exists()


@pytest.mark.parametrize("next_url", ["https://outside.invalid/", "/admin/"])
def test_login_destination_cannot_be_overridden_for_member(client, member, next_url):
    response = client.post(
        reverse("accounts:login") + "?next=" + next_url,
        {**login_data(), "next": next_url},
    )
    assert response.url == reverse("accounts:home")


def test_admin_login_uses_shared_form(client):
    response = client.get(reverse("admin:login") + "?next=/admin/")
    assert response.status_code == 302
    assert response.url == reverse("accounts:login") + "?next=/admin/"


def test_group_membership_without_staff_does_not_open_admin(client, member):
    member.groups.add(Group.objects.get(name="Организаторы"))
    response = client.post(reverse("accounts:login"), login_data())
    assert response.url == reverse("accounts:home")
    response = client.get(reverse("admin:index"), follow=True)
    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("accounts:home")


def test_account_requires_login_and_shows_current_user(client, member):
    response = client.get(reverse("accounts:home"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login") + "?next=")
    client.force_login(member)
    response = client.get(reverse("accounts:home"))
    assert response.status_code == 200
    assert "Анкеты участников" in response.text
    assert member.email in client.get(reverse("accounts:profile")).text


def test_authenticated_normal_login_returns_to_account(client, organizer):
    client.force_login(organizer)
    response = client.get(reverse("accounts:login"))
    assert response.url == reverse("accounts:home")


def test_staff_login_from_admin_returns_to_requested_page(client, organizer):
    destination = (
        reverse("admin:festivals_festival_changelist") + "?status__exact=draft"
    )
    login_page = client.get(destination, follow=True)
    assert "accounts/login.html" in {template.name for template in login_page.templates}
    response = client.post(
        login_page.request["PATH_INFO"] + "?" + login_page.request["QUERY_STRING"],
        login_data(organizer.email),
    )
    assert response.url == destination


@pytest.mark.parametrize(
    "next_url",
    [
        "https://outside.invalid/admin/",
        "//outside.invalid/admin/",
        "https://testserver@outside.invalid/admin/",
        "/accounts/login/",
    ],
)
def test_staff_login_rejects_external_and_non_admin_return(client, organizer, next_url):
    response = client.post(
        reverse("accounts:login"), {**login_data(organizer.email), "next": next_url}
    )
    assert response.url == reverse("accounts:home")


def test_login_requires_csrf(member):
    client = Client(enforce_csrf_checks=True)
    assert client.post(reverse("accounts:login"), login_data()).status_code == 403
    client.get(reverse("accounts:login"))
    token = client.cookies["csrftoken"].value
    response = client.post(
        reverse("accounts:login"), {**login_data(), "csrfmiddlewaretoken": token}
    )
    assert response.url == reverse("accounts:home")


@pytest.mark.parametrize("logout_url", ["accounts:logout", "admin:logout"])
def test_logout_is_post_only_and_returns_to_shared_login(client, member, logout_url):
    client.force_login(member)
    assert client.get(reverse(logout_url)).status_code == 405
    response = client.post(reverse(logout_url))
    assert response.url == reverse("accounts:login")
    assert "_auth_user_id" not in client.session


def test_logout_requires_csrf(member):
    client = Client(enforce_csrf_checks=True)
    client.force_login(member)
    assert client.post(reverse("accounts:logout")).status_code == 403
    assert "_auth_user_id" in client.session
