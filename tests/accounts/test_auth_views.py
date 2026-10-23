import re
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import caches
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError

from accounts.models import User
from accounts.services import PENDING_SESSION_KEY, verification_token

pytestmark = pytest.mark.django_db
PASSWORD = "Synthetic-account-42!xy"


def signup_data(email="new@example.invalid"):
    return {"email": email, "password1": PASSWORD, "password2": PASSWORD}


def email_url(message):
    return re.search(r"https?://\S+", message.body)[0]


@pytest.fixture
def signup(client, django_capture_on_commit_callbacks):
    def submit(data=None):
        with django_capture_on_commit_callbacks(execute=True):
            return client.post(reverse("accounts:register"), data or signup_data())

    return submit


@pytest.fixture
def pending_user(django_user_model):
    return django_user_model.objects.create_user(
        "pending@example.invalid",
        PASSWORD,
        is_active=False,
        email_verification_pending=True,
    )


def test_signup_normalizes_email_sends_link_after_commit_and_has_no_privileges(
    signup, client, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    group = Group.objects.get(name="Организаторы")
    response = signup(
        {
            **signup_data(" New@Example.INVALID "),
            "is_staff": "on",
            "is_superuser": "on",
            "is_active": "on",
            "email_verification_pending": "",
            "email_verified_at": "2030-01-01",
            "groups": [group.pk],
        }
    )
    assert response.url == reverse("accounts:activation_pending")
    user = User.objects.get()
    assert user.email == "new@example.invalid"
    assert user.check_password(PASSWORD)
    assert user.password != PASSWORD
    assert not user.is_active
    assert user.email_verification_pending
    assert user.email_verified_at is None
    assert not user.is_staff and not user.is_superuser
    assert not user.groups.exists() and not user.user_permissions.exists()
    assert "_auth_user_id" not in client.session
    assert client.session[PENDING_SESSION_KEY] == user.pk
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert PASSWORD not in mail.outbox[0].body
    url = email_url(mail.outbox[0])
    assert client.get(url).context["valid_link"]
    user.refresh_from_db()
    assert not user.is_active
    response = client.post(url)
    assert response.url == reverse("accounts:profile")
    user.refresh_from_db()
    assert user.is_active and not user.email_verification_pending
    assert user.email_verified_at is not None
    assert int(client.session["_auth_user_id"]) == user.pk


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"email": "bad-address"}, "email"),
        ({"password2": "different"}, "password2"),
        ({"password1": "123", "password2": "123"}, "password2"),
        ({"password1": "password", "password2": "password"}, "password2"),
    ],
)
def test_signup_form_errors_preserve_email_without_passwords(signup, changes, field):
    data = {**signup_data(), **changes}
    response = signup(data)
    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert response.context["form"]["email"].value() == data["email"]
    assert f'value="{data["password1"]}"' not in response.text
    assert not User.objects.exists()


def test_duplicate_case_variant_is_form_error(signup, member):
    response = signup(signup_data(" MEMBER@EXAMPLE.INVALID "))
    assert response.status_code == 200
    assert "email" in response.context["form"].errors
    assert User.objects.count() == 1
    assert not mail.outbox


@pytest.mark.parametrize("required", [False, True])
def test_confirmation_setting_controls_automatic_login_without_debug(
    signup, client, settings, required
):
    settings.DEBUG = False
    settings.EMAIL_VERIFICATION_REQUIRED = required
    response = signup()
    user = User.objects.get()
    assert user.email_verification_pending
    assert user.email_verified_at is None
    assert len(mail.outbox) == 1
    if required:
        assert response.url == reverse("accounts:activation_pending")
        assert not user.is_active
        assert "_auth_user_id" not in client.session
        pending = client.get(response.url)
        assert "Чтобы войти, подтвердите email" in pending.text
        assert "Пропустить" not in pending.text
        assert client.post(reverse("accounts:activation_skip")).status_code == 404
    else:
        assert response.url == reverse("accounts:home")
        assert user.is_active
        assert int(client.session["_auth_user_id"]) == user.pk
        assert PENDING_SESSION_KEY not in client.session
        home = client.get(response.url)
        assert "Регистрация завершена" in home.text
        profile = client.get(reverse("accounts:profile"))
        assert "Email не подтверждён" in profile.text
        assert "Отправить письмо повторно" in profile.text
        client.logout()
        assert client.login(username=user.email, password=PASSWORD)


def test_automatic_login_does_not_invalidate_confirmation_link(signup, client):
    signup()
    url = email_url(mail.outbox[0])
    assert client.post(url).status_code == 302
    assert User.objects.get().email_verified_at is not None
    assert (
        "Отправить письмо повторно" not in client.get(reverse("accounts:profile")).text
    )


def test_legacy_pending_registration_can_still_skip(client, pending_user, settings):
    settings.EMAIL_VERIFICATION_REQUIRED = False
    session = client.session
    session[PENDING_SESSION_KEY] = pending_user.pk
    session.save()
    assert "Пропустить" in client.get(reverse("accounts:activation_pending")).text
    assert client.get(reverse("accounts:activation_skip")).status_code == 405
    response = client.post(reverse("accounts:activation_skip"), {"user_id": 99999})
    assert response.url == reverse("accounts:home")
    pending_user.refresh_from_db()
    assert pending_user.is_active and pending_user.email_verification_pending
    assert pending_user.email_verified_at is None


def test_enabling_required_confirmation_rejects_skipped_login_and_session(
    signup, client, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = False
    signup()
    assert client.get(reverse("accounts:home")).status_code == 200
    settings.EMAIL_VERIFICATION_REQUIRED = True
    assert client.get(reverse("accounts:home")).status_code == 302
    response = client.post(
        reverse("accounts:login"),
        {"username": "new@example.invalid", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not response.wsgi_request.user.is_authenticated


def test_skip_requires_registration_session_and_csrf(pending_user):
    client = Client()
    assert (
        client.post(
            reverse("accounts:activation_skip"), {"user_id": pending_user.pk}
        ).status_code
        == 403
    )
    csrf_client = Client(enforce_csrf_checks=True)
    session = csrf_client.session
    session[PENDING_SESSION_KEY] = pending_user.pk
    session.save()
    assert csrf_client.post(reverse("accounts:activation_skip")).status_code == 403
    pending_user.refresh_from_db()
    assert not pending_user.is_active


@pytest.mark.parametrize("reason", ["expired", "tampered", "email_changed", "used"])
def test_activation_invalid_links_never_log_in(client, pending_user, settings, reason):
    token = verification_token(pending_user)
    if reason == "tampered":
        token += "x"
    elif reason == "email_changed":
        pending_user.email = "changed@example.invalid"
        pending_user.save()
    elif reason == "used":
        assert (
            client.post(reverse("accounts:activate", args=[token])).status_code == 302
        )
        client.logout()
    url = reverse("accounts:activate", args=[token])
    if reason == "expired":
        with patch(
            "django.core.signing.time.time",
            return_value=time.time() + settings.EMAIL_VERIFICATION_TIMEOUT + 2,
        ):
            response = client.post(url)
    else:
        response = client.post(url)
    assert response.status_code == 400
    assert "_auth_user_id" not in client.session


def test_activation_requires_csrf_and_get_does_not_activate(pending_user):
    client = Client(enforce_csrf_checks=True)
    url = reverse("accounts:activate", args=[verification_token(pending_user)])
    assert client.get(url).status_code == 200
    assert client.post(url).status_code == 403
    pending_user.refresh_from_db()
    assert not pending_user.is_active


@pytest.mark.parametrize("known", [False, True])
def test_resend_response_is_generic(client, pending_user, known):
    email = " PENDING@EXAMPLE.INVALID " if known else "unknown@example.invalid"
    response = client.post(reverse("accounts:activation_resend"), {"email": email})
    assert response.url == reverse("accounts:activation_sent")
    assert len(mail.outbox) == int(known)
    assert not pending_user.is_active


def test_disabled_existing_account_cannot_be_activated(client, member):
    member.is_active = False
    member.save()
    assert not member.email_verification_pending
    client.post(reverse("accounts:activation_resend"), {"email": member.email})
    assert not mail.outbox
    url = reverse("accounts:activate", args=[verification_token(member)])
    assert client.post(url).status_code == 400
    member.refresh_from_db()
    assert not member.is_active


@pytest.mark.parametrize("previously_skipped", [False, True])
def test_admin_block_cancels_pending_activation(
    client, superuser, pending_user, previously_skipped
):
    pending_user.is_active = previously_skipped
    pending_user.save()
    url = reverse("accounts:activate", args=[verification_token(pending_user)])
    client.force_login(superuser)
    response = client.post(
        reverse("admin:accounts_user_change", args=[pending_user.pk]),
        {"email": pending_user.email, "is_active": ""},
    )
    assert response.status_code == 302
    pending_user.refresh_from_db()
    assert not pending_user.is_active
    assert not pending_user.email_verification_pending
    client.logout()
    assert client.post(url).status_code == 400
    client.post(reverse("accounts:activation_resend"), {"email": pending_user.email})
    assert not mail.outbox


@pytest.mark.django_db(transaction=True)
def test_mail_failure_keeps_account_usable_without_logging_secret(
    signup, client, caplog
):
    with patch(
        "accounts.services.EmailMessage.send",
        side_effect=OSError("private@example.invalid token-secret"),
    ):
        response = signup()
    assert response.status_code == 302
    user = User.objects.get()
    assert user.is_active and user.email_verification_pending
    home = client.get(response.url)
    assert "Не удалось отправить письмо подтверждения" in home.text
    assert "Письмо подтверждения отправлено" not in home.text
    assert home.wsgi_request.user.is_authenticated
    assert "delivery failed" in caplog.text
    assert "private@example.invalid" not in caplog.text
    assert "token-secret" not in caplog.text


def test_confirmation_from_account_uses_current_user_and_returns_to_profile(
    signup, client, pending_user
):
    signup()
    mail.outbox.clear()
    response = client.post(
        reverse("accounts:activation_resend"), {"email": pending_user.email}
    )
    assert response.url == reverse("accounts:profile")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new@example.invalid"]
    assert "Письмо подтверждения отправлено" in client.get(response.url).text
    assert client.get(reverse("accounts:activation_resend")).url == reverse(
        "accounts:profile"
    )


def test_account_confirmation_cannot_bypass_email_rate_limit(signup, client, settings):
    signup()
    settings.AUTH_RATE_LIMITS = {"mail": {"email": (1, 60)}}
    url = reverse("accounts:activation_resend")
    assert client.post(url, {"email": "first@example.invalid"}).status_code == 302
    assert client.post(url, {"email": "second@example.invalid"}).status_code == 429


def test_account_confirmation_handles_smtp_failure(signup, client):
    signup()
    with patch("accounts.views.send_verification_email", return_value=False):
        response = client.post(reverse("accounts:activation_resend"))
    assert response.url == reverse("accounts:profile")
    messages = list(get_messages(response.wsgi_request))
    assert any("Не удалось отправить" in str(message) for message in messages)
    assert not any(
        "Письмо подтверждения отправлено" in str(message) for message in messages
    )
    assert client.get(reverse("accounts:home")).wsgi_request.user.is_authenticated


def test_confirmed_account_does_not_receive_another_activation(client, member):
    member.email_verified_at = timezone.now()
    member.save(update_fields=["email_verified_at"])
    client.force_login(member)
    assert client.post(reverse("accounts:activation_resend")).url == reverse(
        "accounts:profile"
    )
    assert not mail.outbox


def test_account_confirmation_requires_csrf(pending_user):
    pending_user.is_active = True
    pending_user.save()
    client = Client(enforce_csrf_checks=True)
    client.force_login(pending_user)
    assert client.post(reverse("accounts:activation_resend")).status_code == 403
    assert not mail.outbox


def test_account_navigation_and_registration_field_hints(client):
    login_page = client.get(reverse("accounts:login"))
    assert "Регистрация" in login_page.text
    assert "Создать аккаунт" not in login_page.text
    assert reverse("accounts:activation_resend") not in login_page.text
    for route in ("register", "password_reset"):
        response = client.get(reverse(f"accounts:{route}"))
        assert "Ко входу" in response.text
        assert "На главную" not in response.text
        # Only the header logo links to the home page.
        assert response.text.count('href="/"') == 1
    registration = client.get(reverse("accounts:register"))
    form = registration.context["form"]
    assert not form.fields["password1"].help_text
    assert form.fields["password2"].help_text
    assert "Для подтверждения введите" not in registration.text
    assert registration.text.index('name="password2"') < registration.text.index(
        'id="id_password2_helptext"'
    )
    assert 'aria-describedby="id_password2_helptext"' in registration.text
    assert form.fields["email"].widget.attrs["autocomplete"] == "username"
    assert (
        login_page.context["form"].fields["username"].widget.attrs["autocomplete"]
        == "username"
    )
    for field in ("password1", "password2"):
        assert form.fields[field].widget.attrs["autocomplete"] == "new-password"
    reset = client.get(reverse("accounts:password_reset"))
    assert reset.context["form"].fields["email"].widget.attrs["autocomplete"] == "email"


def test_new_password_rules_follow_confirmation_field(client, member):
    client.post(reverse("accounts:password_reset"), {"email": member.email})
    response = client.get(email_url(mail.outbox[0]), follow=True)
    form = response.context["form"]
    assert not form.fields["new_password1"].help_text
    assert form.fields["new_password2"].help_text
    assert response.text.index('name="new_password2"') < response.text.index(
        'id="id_new_password2_helptext"'
    )


def test_favicon_is_connected_to_public_and_admin_pages(client, superuser):
    assert "/static/img/favicon.svg" in client.get(reverse("accounts:login")).text
    client.force_login(superuser)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert "/static/img/favicon.svg" in response.text


@pytest.mark.parametrize("fixture_name", ["member", "organizer", "superuser"])
def test_existing_roles_still_login_when_verification_required(
    client, request, settings, fixture_name
):
    user = request.getfixturevalue(fixture_name)
    settings.EMAIL_VERIFICATION_REQUIRED = True
    response = client.post(
        reverse("accounts:login"),
        {"username": user.email, "password": "synthetic-account-password-42"},
    )
    assert response.url == reverse("accounts:home")


@pytest.mark.parametrize("fixture_name", ["member", "organizer", "superuser"])
def test_password_reset_end_to_end(client, request, fixture_name):
    user = request.getfixturevalue(fixture_name)
    other_client = Client()
    other_client.force_login(user)
    response = client.post(
        reverse("accounts:password_reset"), {"email": f" {user.email.upper()} "}
    )
    assert response.url == reverse("accounts:password_reset_done")
    assert len(mail.outbox) == 1
    url = email_url(mail.outbox[0])
    response = client.get(url, follow=True)
    assert response.context["validlink"]
    confirm_url = response.request["PATH_INFO"]
    assert (
        client.post(
            confirm_url, {"new_password1": "123", "new_password2": "123"}
        ).status_code
        == 200
    )
    response = client.post(
        confirm_url,
        {"new_password1": PASSWORD, "new_password2": PASSWORD},
    )
    assert response.url == reverse("accounts:password_reset_complete")
    assert "_auth_user_id" not in client.session
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert not user.check_password("synthetic-account-password-42")
    assert not Client().get(url, follow=True).context["validlink"]
    assert other_client.get(reverse("accounts:home")).status_code == 302


@pytest.mark.parametrize("kind", ["unknown", "inactive", "unusable"])
def test_password_reset_does_not_disclose_ineligible_accounts(client, member, kind):
    email = member.email
    if kind == "unknown":
        email = "unknown@example.invalid"
    elif kind == "inactive":
        member.is_active = False
        member.save()
    else:
        member.set_unusable_password()
        member.save()
    response = client.post(reverse("accounts:password_reset"), {"email": email})
    assert response.url == reverse("accounts:password_reset_done")
    assert not mail.outbox


@pytest.mark.parametrize("reason", ["expired", "tampered"])
def test_invalid_reset_tokens(client, member, reason):
    client.post(reverse("accounts:password_reset"), {"email": member.email})
    url = email_url(mail.outbox[0])
    if reason == "tampered":
        url = url.rstrip("/") + "x/"
        response = client.get(url, follow=True)
    else:
        with patch.object(
            default_token_generator,
            "_now",
            return_value=datetime.now() + timedelta(hours=25),
        ):
            response = client.get(url, follow=True)
    assert not response.context["validlink"]


@pytest.mark.parametrize("route", ["register", "activation_resend", "password_reset"])
def test_public_account_forms_require_csrf(route):
    client = Client(enforce_csrf_checks=True)
    assert client.post(reverse(f"accounts:{route}"), signup_data()).status_code == 403


def test_rate_limits_are_shared_and_ignore_forwarded_header(client, settings):
    settings.AUTH_RATE_LIMITS = {"mail": {"email": (1, 60), "ip": (10, 60)}}
    url = reverse("accounts:password_reset")
    assert client.post(url, {"email": "same@example.invalid"}).status_code == 302
    response = Client().post(
        url, {"email": " SAME@EXAMPLE.INVALID "}, HTTP_X_FORWARDED_FOR="different"
    )
    assert response.status_code == 429
    assert response["Retry-After"] == "60"
    assert "same@example.invalid" not in response.text


def test_rate_limit_expiry(client, settings):
    settings.AUTH_RATE_LIMITS = {"register": {"ip": (1, 60)}}
    url = reverse("accounts:register")
    assert client.post(url, {}).status_code == 200
    assert client.post(url, {}).status_code == 429
    with patch(
        "django.core.cache.backends.locmem.time.time", return_value=time.time() + 61
    ):
        assert client.post(url, {}).status_code == 200


def test_redis_failure_blocks_new_attempt_but_keeps_existing_session(client, member):
    client.force_login(member)
    with patch.object(
        caches["auth"], "add", side_effect=ConnectionError("unavailable")
    ):
        assert Client().post(reverse("accounts:login"), {}).status_code == 503
        assert client.get(reverse("accounts:home")).status_code == 200
