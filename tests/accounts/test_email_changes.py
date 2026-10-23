import re
import time
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

pytestmark = pytest.mark.django_db
PASSWORD = "synthetic-account-password-42"
NEW_EMAIL = "new-address@example.invalid"


def email_url(message):
    return re.search(r"https?://\S+", message.body)[0]


@pytest.fixture
def change(client, member, django_capture_on_commit_callbacks):
    member.email_verified_at = timezone.now()
    member.save(update_fields=["email_verified_at"])
    client.force_login(member)

    def submit(email=NEW_EMAIL, password=PASSWORD):
        with django_capture_on_commit_callbacks(execute=True):
            return client.post(
                reverse("accounts:email_change"),
                {"new_email": email, "current_password": password},
            )

    return submit


@pytest.mark.parametrize("required", [False, True])
def test_email_change_modes_login_recovery_and_confirmation(
    change, client, member, settings, required
):
    settings.EMAIL_VERIFICATION_REQUIRED = required
    response = change(" NEW-ADDRESS@EXAMPLE.INVALID ")
    assert response.url == reverse("accounts:profile")
    member.refresh_from_db()
    assert member.email == ("member@example.invalid" if required else NEW_EMAIL)
    assert member.pending_email == (NEW_EMAIL if required else "")
    assert (member.email_verified_at is not None) == required
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [NEW_EMAIL]
    link = email_url(mail.outbox[0])
    profile = client.get(response.url)
    assert profile.wsgi_request.user.is_authenticated
    if required:
        assert "Пока используйте прежний адрес" in profile.text
        assert Client().login(username=member.email, password=PASSWORD)
        assert not Client().login(username=NEW_EMAIL, password=PASSWORD)
    else:
        assert "Теперь для входа используйте" in profile.text
        assert not Client().login(username="member@example.invalid", password=PASSWORD)
        assert Client().login(username=NEW_EMAIL, password=PASSWORD)
    client.post(reverse("accounts:password_reset"), {"email": member.email})
    assert mail.outbox[-1].to == [member.email]
    before = member.email
    assert client.get(link).context["valid_link"]
    member.refresh_from_db()
    assert member.email == before
    assert client.post(link).url == reverse("accounts:profile")
    member.refresh_from_db()
    assert member.email == NEW_EMAIL and member.email_verified_at is not None
    assert not member.pending_email and member.email_change_id is None
    assert client.post(link).status_code == 400
    assert not Client().login(username="member@example.invalid", password=PASSWORD)
    assert Client().login(username=NEW_EMAIL, password=PASSWORD)
    client.post(reverse("accounts:password_reset"), {"email": NEW_EMAIL})
    assert mail.outbox[-1].to == [NEW_EMAIL]
    assert "Email подтверждён" in client.get(reverse("accounts:profile")).text


def test_wrong_password_duplicate_and_unchanged_email_do_not_change_account(
    change, client, member, django_user_model
):
    django_user_model.objects.create_user("taken@example.invalid", PASSWORD)
    response = change(password="Wrong-synthetic-password!")
    assert response.status_code == 200
    assert "current_password" in response.context["email_change_form"].errors
    assert "Wrong-synthetic-password!" not in response.text
    response = change(" TAKEN@EXAMPLE.INVALID ")
    assert response.status_code == 200
    assert "new_email" in response.context["email_change_form"].errors
    assert response.context["form"]["first_name"].value() == ""
    assert change(" MEMBER@EXAMPLE.INVALID ").url == reverse("accounts:profile")
    member.refresh_from_db()
    assert member.email == "member@example.invalid"
    assert member.email_verified_at is not None
    assert not member.pending_email and not mail.outbox
    assert client.get(reverse("accounts:home")).wsgi_request.user.is_authenticated


def test_pending_request_is_preserved_when_policy_or_current_email_is_saved(
    change, client, member, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    change()
    member.refresh_from_db()
    request_id, verified_at = member.email_change_id, member.email_verified_at
    link = email_url(mail.outbox[-1])
    settings.EMAIL_VERIFICATION_REQUIRED = False
    assert client.get(reverse("accounts:profile")).status_code == 200
    change(member.email)
    member.refresh_from_db()
    assert member.email == "member@example.invalid"
    assert member.pending_email == NEW_EMAIL
    assert member.email_change_id == request_id
    assert member.email_verified_at == verified_at
    assert len(mail.outbox) == 1
    # Resubmitting the new address explicitly applies the current policy.
    change()
    member.refresh_from_db()
    assert member.email == NEW_EMAIL and not member.pending_email
    assert client.post(link).status_code == 400


@pytest.mark.parametrize("other_account", [False, True])
def test_pending_confirmation_does_not_log_in_or_switch_another_session(
    change, member, settings, other_account
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    change()
    visitor = Client()
    other = User.objects.create_user("other@example.invalid", PASSWORD)
    if other_account:
        visitor.force_login(other)
    response = visitor.post(email_url(mail.outbox[-1]))
    assert response.status_code == 200 and response.context["changed"]
    assert response.wsgi_request.user.is_authenticated == other_account
    if other_account:
        assert response.wsgi_request.user.pk == other.pk
    member.refresh_from_db()
    assert member.email == NEW_EMAIL and member.email_verified_at is not None


@pytest.mark.parametrize(
    "reason",
    ["tampered", "expired", "cancelled", "replaced", "blocked", "password_changed"],
)
def test_invalid_change_links_do_not_replace_email(
    change, client, member, superuser, settings, reason
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    change()
    link = email_url(mail.outbox[0])
    if reason == "tampered":
        link = link.rstrip("/") + "x/"
    elif reason == "cancelled":
        assert client.post(reverse("accounts:email_change_cancel")).status_code == 302
    elif reason == "replaced":
        change("replacement@example.invalid")
    elif reason == "blocked":
        client.force_login(superuser)
        client.post(
            reverse("admin:accounts_user_change", args=[member.pk]),
            {"email": member.email, "is_active": ""},
        )
        member.refresh_from_db()
        assert not member.pending_email and member.email_change_id is None
    elif reason == "password_changed":
        member.set_password("Changed-synthetic-password!")
        member.save(update_fields=["password"])
    if reason == "expired":
        with patch(
            "django.core.signing.time.time",
            return_value=time.time() + settings.EMAIL_VERIFICATION_TIMEOUT + 2,
        ):
            response = client.post(link)
    else:
        response = client.post(link)
    assert response.status_code == 400
    member.refresh_from_db()
    assert member.email == "member@example.invalid"
    if reason == "blocked":
        assert not member.is_active


def test_legacy_confirmation_status_greeting_and_resend_keep_access(
    client, member, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    client.force_login(member)
    url = reverse("accounts:profile")
    profile = client.get(url)
    assert f"Здравствуйте, {member.email}!" in profile.text
    assert "Email не подтверждён" in profile.text
    assert "Отправить письмо повторно" in profile.text
    assert client.post(reverse("accounts:activation_resend")).url == url
    member.refresh_from_db()
    assert not member.email_verification_pending
    assert client.get(url).status_code == 200
    assert client.post(email_url(mail.outbox[-1])).url == url
    assert "email-status-confirmed" in client.get(url).text
    member.first_name = " Анна "
    member.save(update_fields=["first_name"])
    assert "Здравствуйте, Анна!" in client.get(reverse("accounts:home")).text
    count = len(mail.outbox)
    client.post(reverse("accounts:activation_resend"))
    assert len(mail.outbox) == count


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("required", [False, True])
def test_mail_failure_preserves_mode_and_allows_retry(
    change, client, member, settings, required
):
    settings.EMAIL_VERIFICATION_REQUIRED = required
    with patch("accounts.services.EmailMessage.send", side_effect=OSError("synthetic")):
        response = change()
    assert "Не удалось отправить письмо" in client.get(response.url).text
    member.refresh_from_db()
    assert member.email == ("member@example.invalid" if required else NEW_EMAIL)
    route = "email_change_resend" if required else "activation_resend"
    assert client.post(reverse(f"accounts:{route}")).url == reverse("accounts:profile")
    assert mail.outbox[-1].to == [NEW_EMAIL]
    assert client.post(email_url(mail.outbox[-1])).url == reverse("accounts:profile")
    member.refresh_from_db()
    assert member.email == NEW_EMAIL and member.email_verified_at is not None


def test_change_controls_require_login_csrf_and_share_mail_limits(
    change, client, member, settings
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    change()
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(member)
    for route in ("email_change", "email_change_resend", "email_change_cancel"):
        url = reverse(f"accounts:{route}")
        assert Client().post(url, {}).url.startswith(reverse("accounts:login"))
        assert csrf_client.post(url, {}).status_code == 403
    assert csrf_client.post(email_url(mail.outbox[0])).status_code == 403
    settings.AUTH_RATE_LIMITS = {"mail": {"email": (2, 60)}}
    # A confirmation request and password reset share the same target-email budget.
    assert client.post(reverse("accounts:email_change_resend")).status_code == 302
    assert (
        client.post(
            reverse("accounts:password_reset"), {"email": NEW_EMAIL}
        ).status_code
        == 429
    )
    assert User.objects.get(pk=member.pk).email == member.email
