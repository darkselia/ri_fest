import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from pytest_django.asserts import assertContains

pytestmark = pytest.mark.django_db
OLD_PASSWORD = "synthetic-account-password-42"
NEW_PASSWORD = "Changed-synthetic-password-73!"


@pytest.mark.parametrize("change", ["block", "delete", "email", "password"])
def test_password_change_rechecks_concurrent_account_changes(
    client, member, monkeypatch, change
):
    from accounts.models import User
    from accounts.views import AccountPasswordChangeView

    client.force_login(member)
    original_dispatch = AccountPasswordChangeView.dispatch

    def change_after_authentication(self, request, *args, **kwargs):
        user_id = request.user.pk
        if change == "delete":
            User.objects.filter(pk=user_id).delete()
        elif change == "password":
            current = User.objects.get(pk=user_id)
            current.set_password("Concurrent-synthetic-password-95!")
            current.save(update_fields=["password"])
        else:
            User.objects.filter(pk=user_id).update(
                **(
                    {"is_active": False}
                    if change == "block"
                    else {"email": "new@example.invalid"}
                )
            )
        return original_dispatch(self, request, *args, **kwargs)

    monkeypatch.setattr(
        AccountPasswordChangeView, "dispatch", change_after_authentication
    )
    response = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": OLD_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )
    if change == "delete":
        assert response.status_code == 403
        assert not User.objects.filter(pk=member.pk).exists()
        return
    member.refresh_from_db()
    if change == "block":
        assert response.status_code == 403
        assert not member.is_active and member.check_password(OLD_PASSWORD)
    elif change == "password":
        assert response.status_code == 200
        assert "old_password" in response.context["password_form"].errors
        assert member.check_password("Concurrent-synthetic-password-95!")
    else:
        assert response.status_code == 302
        assert member.email == "new@example.invalid"
        assert member.check_password(NEW_PASSWORD)


def test_password_reset_does_not_overwrite_concurrent_account_changes(member):
    from accounts.forms import AccountSetPasswordForm
    from accounts.models import User

    form = AccountSetPasswordForm(
        member, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
    )
    assert form.is_valid()
    User.objects.filter(pk=member.pk).update(
        is_active=False, email="new@example.invalid"
    )
    form.save()
    member.refresh_from_db()
    assert not member.is_active and member.email == "new@example.invalid"
    assert member.check_password(NEW_PASSWORD)


def test_profile_saves_and_clears_only_own_names(client, member, django_user_model):
    other = django_user_model.objects.create_user(
        "other@example.invalid", OLD_PASSWORD, first_name="Другой"
    )
    member.first_name = "Прежнее"
    member.save(update_fields=["first_name"])
    client.force_login(member)
    url = reverse("accounts:profile")
    assert client.get(url).context["form"].initial["first_name"] == "Прежнее"
    response = client.post(
        url,
        {
            "first_name": " Анна ",
            "last_name": " Иванова ",
            "user_id": other.pk,
            "email": "changed@example.invalid",
            "is_staff": "on",
            "is_superuser": "on",
            "is_active": "",
            "email_verification_pending": "on",
        },
    )
    assert response.url == reverse("accounts:profile")
    member.refresh_from_db()
    other.refresh_from_db()
    assert (member.first_name, member.last_name) == ("Анна", "Иванова")
    assert other.first_name == "Другой"
    assert member.email == "member@example.invalid"
    assert not member.is_staff and not member.is_superuser
    assert member.is_active and not member.email_verification_pending
    assert member.check_password(OLD_PASSWORD)
    home = client.get(response.url)
    assert "Личные данные сохранены" in home.text
    assert 'value="Анна"' in home.text and 'value="Иванова"' in home.text
    assert client.post(url, {"first_name": "", "last_name": ""}).status_code == 302
    member.refresh_from_db()
    assert member.first_name == member.last_name == ""
    assert client.get(url).context["form"].initial["first_name"] == ""


def test_account_settings_require_login_and_csrf(client, member):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(member)
    for route in ("profile", "password_change"):
        url = reverse(f"accounts:{route}")
        assert client.get(url).url.startswith(reverse("accounts:login"))
        assert client.post(url, {}).url.startswith(reverse("accounts:login"))
        assert csrf_client.post(url, {}).status_code == 403
    member.refresh_from_db()
    assert member.first_name == member.last_name == ""
    assert member.check_password(OLD_PASSWORD)


def test_password_change_rejects_wrong_password_and_confirmation(client, member):
    client.force_login(member)
    url = reverse("accounts:password_change")
    data = {
        "old_password": OLD_PASSWORD,
        "new_password1": NEW_PASSWORD,
        "new_password2": NEW_PASSWORD,
    }
    for changes, field in (
        ({"old_password": "Wrong-synthetic-password!"}, "old_password"),
        ({"new_password2": "Different-synthetic-password!"}, "new_password2"),
    ):
        response = client.post(url, {**data, **changes})
        assert response.status_code == 200
        assert field in response.context["password_form"].errors
        assert f'value="{NEW_PASSWORD}"' not in response.text
    member.refresh_from_db()
    assert member.check_password(OLD_PASSWORD)
    assert not mail.outbox


def test_password_change_keeps_current_session_and_ends_other_without_mail(
    client, member, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = False
    member.email_verification_pending = True
    member.save(update_fields=["email_verification_pending"])
    client.force_login(member)
    other_client = Client()
    other_client.force_login(member)
    session = client.session
    session["synthetic_marker"] = "preserved"
    session.save()
    response = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": OLD_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )
    assert response.url == reverse("accounts:profile")
    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD)
    assert not member.check_password(OLD_PASSWORD)
    assert member.email_verification_pending and member.email_verified_at is None
    home = client.get(response.url)
    assert home.wsgi_request.user.is_authenticated
    assert "Пароль изменён" in home.text
    assert client.session["synthetic_marker"] == "preserved"
    assert other_client.get(reverse("accounts:home")).status_code == 302
    assert not Client().login(username=member.email, password=OLD_PASSWORD)
    assert Client().login(username=member.email, password=NEW_PASSWORD)
    assert not mail.outbox


@pytest.mark.parametrize("fixture_name", ["member", "organizer"])
def test_account_sections_share_shell_navigation_and_community_links(
    client, request, fixture_name
):
    user = request.getfixturevalue(fixture_name)
    client.force_login(user)
    for route, tab in (
        ("home", "applications"),
        ("profile", "profile"),
        ("password_change", "profile"),
    ):
        response = client.get(reverse(f"accounts:{route}"))
        assert response.status_code == 200
        assert response.context["account_tab"] == tab
        assert "layouts/cabinet.html" in {
            template.name for template in response.templates
        }
        section_navigation = response.text.split(
            'aria-label="Разделы личного кабинета"', 1
        )[1].split("</nav>", 1)[0]
        assert section_navigation.count('aria-current="page"') == 1
        active_route = "profile" if route == "password_change" else route
        assert (
            f'href="{reverse(f"accounts:{active_route}")}" aria-current="page"'
            in section_navigation
        )
        assert ("Открыть админку" in response.text) == user.is_staff
        assert "Выйти" in response.text
        for url, label in (
            ("https://vk.ru/ri.fest", "Группа ВКонтакте"),
            ("https://t.me/+J_oZLtGqPu81OTE6", "Telegram-чат участников"),
            ("https://vk.me/join/AZQ1d9T6Byfbg2LPehxencDr", "VK-чат участников"),
        ):
            assertContains(
                response,
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>',
                html=True,
            )
