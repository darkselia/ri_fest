import re
from datetime import date

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.tokens import default_token_generator
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from accounts.services import (
    AccountDeletionConflict,
    change_email,
    delete_account,
    send_email_change_confirmation,
    verification_token,
)
from applications.models import Application, ApplicationQuerySet
from festivals.models import Festival

pytestmark = pytest.mark.django_db
PASSWORD = "synthetic-account-password-42"


@pytest.fixture
def history(member):
    records = []
    for status in ("draft", "open", "archived"):
        festival = Festival.objects.create(
            name=f"Синтетический {status}",
            status=status,
            start_date=date(2035, 1, 1),
            end_date=date(2035, 1, 3),
            published_at=timezone.now() if status != "draft" else None,
        )
        records.append(
            Application.objects.create(
                owner=member,
                festival=festival,
                status="draft" if status == "draft" else "active",
            )
        )
    return records


def test_self_deletion_anonymizes_history_revokes_current_and_invalidates_access(
    client, member, history, superuser
):
    content_type = ContentType.objects.get_for_model(User)
    LogEntry.objects.create(
        user=superuser,
        content_type=content_type,
        object_id=str(member.pk),
        object_repr=member.email,
        action_flag=CHANGE,
        change_message=member.email,
    )
    other_session = Client()
    other_session.force_login(member)
    client.force_login(member)
    link = reverse("accounts:activate", args=[verification_token(member)])
    original_id, email = member.pk, member.email
    url = reverse("accounts:delete")
    assert client.get(url).status_code == 200
    assert User.objects.filter(pk=original_id).exists()
    response = client.post(url, {"current_password": "wrong-synthetic-password"})
    assert response.status_code == 200
    assert "wrong-synthetic-password" not in response.text
    assert all(item.owner_id == original_id for item in history)
    assert client.post(url, {"current_password": PASSWORD}).url == reverse(
        "accounts:login"
    )
    assert not User.objects.filter(pk=original_id).exists()
    assert "_auth_user_id" not in client.session
    assert other_session.get(reverse("accounts:profile")).url.startswith(
        reverse("accounts:login")
    )
    assert client.post(link).status_code == 400
    assert not Application.objects.filter(pk=history[0].pk).exists()
    history[2].refresh_from_db()
    assert history[2].status == "archived" and history[2].owner_id is None
    history[1].refresh_from_db()
    assert history[1].owner_id is None and history[1].anonymized_at is not None
    assert history[1].status == "withdrawn"
    log = LogEntry.objects.get(content_type=content_type, object_id=str(original_id))
    assert email not in log.object_repr + log.change_message
    replacement = User.objects.create_user(email, PASSWORD)
    assert replacement.pk != original_id
    assert not Application.objects.for_owner(replacement).exists()


@pytest.mark.parametrize("role", ["member", "organizer", "superuser"])
def test_all_roles_including_last_superuser_can_delete_themselves(
    client, request, role
):
    user = request.getfixturevalue(role)
    user_id = user.pk
    client.force_login(user)
    assert (
        client.post(
            reverse("accounts:delete"), {"current_password": PASSWORD}
        ).status_code
        == 302
    )
    assert not User.objects.filter(pk=user_id).exists()


def test_delete_requires_login_csrf_and_uses_current_account(client, member, superuser):
    url = reverse("accounts:delete")
    assert client.get(url).url.startswith(reverse("accounts:login"))
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(member)
    assert secure.post(url, {"current_password": PASSWORD}).status_code == 403
    with pytest.raises(PermissionDenied):
        delete_account(superuser.pk, actor=member, password=PASSWORD)
    client.force_login(member)
    client.post(url, {"current_password": PASSWORD, "user_id": superuser.pk})
    assert User.objects.filter(pk=superuser.pk).exists()


def test_admin_deletion_uses_same_service_and_admin_password(
    client, superuser, member, history
):
    client.force_login(superuser)
    target_id = member.pk
    member.set_password("different-synthetic-password")
    member.save(update_fields=["password"])
    url = reverse("admin:accounts_user_delete_account", args=[target_id])
    assert client.get(url).status_code == 200
    assert (
        "delete_selected"
        not in client.get(reverse("admin:accounts_user_changelist")).text
    )
    assert (
        client.post(reverse("admin:accounts_user_delete", args=[target_id])).status_code
        == 403
    )
    assert client.post(url, {"current_password": PASSWORD}).url == reverse(
        "admin:accounts_user_changelist"
    )
    assert not User.objects.filter(pk=target_id).exists()
    assert Application.objects.filter(owner__isnull=True).count() == 2


def test_deletion_rollback_restores_account_applications_and_admin_history(
    member, history, monkeypatch
):
    before = list(Application.objects.order_by("pk").values())
    log = LogEntry.objects.create(
        user=member,
        content_type=ContentType.objects.get_for_model(User),
        object_id=str(member.pk),
        object_repr=member.email,
        action_flag=CHANGE,
        change_message=member.email,
    )

    def failed_delete(*args, **kwargs):
        raise RuntimeError("synthetic deletion failure")

    monkeypatch.setattr(User, "delete", failed_delete)
    with pytest.raises(RuntimeError, match="synthetic"):
        delete_account(member.pk, actor=member, password=PASSWORD)
    assert User.objects.filter(pk=member.pk).exists()
    assert list(Application.objects.order_by("pk").values()) == before
    log.refresh_from_db()
    assert log.object_repr == log.change_message == member.email


@pytest.mark.parametrize("orphan", [False, True])
def test_database_rejects_incomplete_anonymization(member, history, orphan):
    changes = {"owner": None} if orphan else {"anonymized_at": timezone.now()}
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=history[0].pk).update(**changes)


def test_password_attempts_share_login_limit(client, member, settings):
    settings.AUTH_RATE_LIMITS = {"login": {"email_ip": (1, 60)}}
    client.force_login(member)
    url = reverse("accounts:delete")
    assert client.post(url, {"current_password": "wrong"}).status_code == 200
    assert client.post(url, {"current_password": PASSWORD}).status_code == 429
    assert User.objects.filter(pk=member.pk).exists()


def test_old_reset_and_email_change_links_cannot_restore_deleted_account(
    client, member, settings, rf
):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    pending, _ = change_email(member.pk, "pending@example.invalid", PASSWORD)
    send_email_change_confirmation(pending, rf.get("/"))
    change_link = re.search(r"https?://\S+", mail.outbox[-1].body)[0]
    reset_link = reverse(
        "accounts:password_reset_confirm",
        args=[
            urlsafe_base64_encode(force_bytes(member.pk)),
            default_token_generator.make_token(member),
        ],
    )
    delete_account(member.pk, actor=member, password=PASSWORD)
    assert client.post(change_link).status_code == 400
    assert not client.get(reset_link).context["validlink"]


def test_calendar_archive_is_respected_before_hourly_sync(member, history):
    current = history[1]
    Festival.objects.filter(pk=current.festival_id).update(
        start_date=date(2020, 1, 1), end_date=date(2020, 1, 3)
    )
    delete_account(member.pk, actor=member, password=PASSWORD)
    current.refresh_from_db()
    assert current.status == "archived" and current.owner_id is None


def test_new_parent_relation_during_deletion_causes_atomic_conflict(
    member, history, monkeypatch
):
    original = ApplicationQuerySet.select_for_update

    def changed_relations(queryset, *args, **kwargs):
        festival = Festival.objects.create(name="Новая связь")
        Application.objects.create(owner=member, festival=festival)
        return original(queryset, *args, **kwargs)

    before = list(Application.objects.order_by("pk").values())
    monkeypatch.setattr(ApplicationQuerySet, "select_for_update", changed_relations)
    with pytest.raises(AccountDeletionConflict):
        delete_account(member.pk, actor=member, password=PASSWORD)
    assert User.objects.filter(pk=member.pk).exists()
    assert list(Application.objects.order_by("pk").values()) == before
