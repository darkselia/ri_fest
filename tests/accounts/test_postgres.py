import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import pytest
from django.core import mail
from django.core.cache import caches
from django.db import close_old_connections, connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.forms import RegistrationForm
from accounts.models import User
from accounts.services import delete_account
from applications.models import Application
from festivals.models import Festival
from festivals.services import (
    FestivalActionConflict,
    delete_festival,
    festival_action_preview,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires isolated PostgreSQL"
    ),
]


def test_concurrent_account_and_festival_deletion_is_atomic():
    user = User.objects.create_user(
        "parallel-owner@example.invalid", "Synthetic-owner-42!"
    )
    admin = User.objects.create_superuser("parallel-admin@example.invalid")
    festival = Festival.objects.create(name="Синтетическое удаление")
    application = Application.objects.create(owner=user, festival=festival)
    barrier = Barrier(2)

    def run(operation):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            operation()
        except FestivalActionConflict:
            return "stale"
        finally:
            close_old_connections()

    operations = [
        lambda: delete_account(user.pk, actor=user, password="Synthetic-owner-42!"),
        lambda: delete_festival(
            festival.pk,
            actor=admin,
            confirmation=festival_action_preview(
                festival.pk, actor=admin, action="delete"
            )["confirmation"],
        ),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, operations))
    assert not User.objects.filter(pk=user.pk).exists()
    if Festival.objects.filter(pk=festival.pk).exists():
        delete_festival(
            festival.pk,
            actor=admin,
            confirmation=festival_action_preview(
                festival.pk, actor=admin, action="delete"
            )["confirmation"],
        )
    assert not Festival.objects.filter(pk=festival.pk).exists()
    assert not Application.objects.filter(pk=application.pk).exists()


@pytest.mark.parametrize("required", [False, True])
def test_concurrent_signup_is_one_account_and_one_form_error(
    monkeypatch, settings, required
):
    settings.EMAIL_VERIFICATION_REQUIRED = required
    barrier = Barrier(2)
    original_save = RegistrationForm.save

    def simultaneous_save(form, commit=True):
        user = original_save(form, commit=commit)
        barrier.wait(timeout=10)
        return user

    monkeypatch.setattr(RegistrationForm, "save", simultaneous_save)
    monkeypatch.setattr(
        "accounts.views.send_verification_email", lambda user, request: True
    )

    def register(email):
        close_old_connections()
        try:
            response = Client().post(
                reverse("accounts:register"),
                {
                    "email": email,
                    "password1": "Synthetic-account-42!xy",
                    "password2": "Synthetic-account-42!xy",
                },
            )
            if response.status_code == 200:
                assert "email" in response.context["form"].errors
            return response.status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(register, ["race@example.invalid", " RACE@EXAMPLE.INVALID "])
        )
    assert sorted(results) == [200, 302]
    user = User.objects.get()
    assert user.email == "race@example.invalid"
    assert user.email_verification_pending
    assert user.is_active == (not required)


def test_real_redis_counter_is_shared_between_clients(settings):
    settings.CACHES = {
        **settings.CACHES,
        "auth": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://redis:6379/2",
            "KEY_PREFIX": f"synthetic-auth-{uuid4().hex}",
        },
    }
    settings.AUTH_RATE_LIMITS = {"register": {"ip": (1, 60)}}
    key = "register:ip:" + hashlib.sha256(b"127.0.0.1").hexdigest()
    cache = caches["auth"]
    try:
        first, second = Client(), Client()
        url = reverse("accounts:register")
        assert first.post(url, {}).status_code == 200
        assert second.post(url, {}).status_code == 429
        assert cache.get(key) == 2
    finally:
        # Delete only this test's key, never flush the site's Redis database.
        cache.delete(key)


@pytest.mark.parametrize("required", [False, True])
def test_smtp_delivery_activation_and_reset_through_mailpit(client, settings, required):
    settings.MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {"host": "mailpit", "port": 1025, "timeout": 5},
        },
    }
    settings.EMAIL_VERIFICATION_REQUIRED = required
    email = f"smtp-{uuid4().hex}@example.invalid"

    def mailbox_json(path):
        with urlopen(f"http://mailpit:8025/api/v1/{path}", timeout=5) as response:
            return json.load(response)

    def delivered_message(subject):
        # Search only this synthetic recipient; never read somebody else's mail.
        result = mailbox_json("search?" + urlencode({"query": f"to:{email}"}))
        summary = next(
            item for item in result["messages"] if item["Subject"] == subject
        )
        message = mailbox_json(f"message/{summary['ID']}")
        assert message["To"][0]["Address"] == email
        return message

    response = client.post(
        reverse("accounts:register"),
        {
            "email": email,
            "password1": "Synthetic-account-42!xy",
            "password2": "Synthetic-account-42!xy",
        },
    )
    assert response.url == reverse(
        "accounts:activation_pending" if required else "accounts:home"
    )
    if not required:
        assert client.get(response.url).wsgi_request.user.is_authenticated
        assert client.post(reverse("accounts:activation_resend")).url == reverse(
            "accounts:profile"
        )
    message = delivered_message("Подтверждение email — РИ-фест")
    activation_url = re.search(r"https?://\S+", message["Text"])[0]
    assert client.post(activation_url).url == reverse("accounts:profile")
    user = User.objects.get(email=email)
    assert user.email_verified_at is not None

    assert (
        client.post(reverse("accounts:password_reset"), {"email": email}).status_code
        == 302
    )
    message = delivered_message("Восстановление пароля — РИ-фест")
    reset_url = re.search(r"https?://\S+", message["Text"])[0]
    response = client.get(reset_url, follow=True)
    assert response.context["validlink"]
    response = client.post(
        response.request["PATH_INFO"],
        {
            "new_password1": "Changed-synthetic-password-42!",
            "new_password2": "Changed-synthetic-password-42!",
        },
    )
    assert response.url == reverse("accounts:password_reset_complete")
    user.refresh_from_db()
    assert user.check_password("Changed-synthetic-password-42!")
    client.force_login(user)
    new_email = "changed-" + email
    assert client.post(
        reverse("accounts:email_change"),
        {"new_email": new_email, "current_password": "Changed-synthetic-password-42!"},
    ).url == reverse("accounts:profile")
    email = new_email
    subject = (
        "Подтверждение нового email — РИ-фест"
        if required
        else "Подтверждение email — РИ-фест"
    )
    message = delivered_message(subject)
    change_url = re.search(r"https?://\S+", message["Text"])[0]
    assert client.post(change_url).url == reverse("accounts:profile")
    user.refresh_from_db()
    assert user.email == new_email and user.email_verified_at is not None


def test_concurrent_email_confirmation_preserves_unique_login(settings, monkeypatch):
    settings.EMAIL_VERIFICATION_REQUIRED = True
    target = "shared-new-address@example.invalid"
    links = []
    for index in range(2):
        user = User.objects.create_user(
            f"owner-{index}@example.invalid",
            "Synthetic-password-42!",
            email_verified_at=timezone.now(),
        )
        client = Client()
        client.force_login(user)
        assert (
            client.post(
                reverse("accounts:email_change"),
                {"new_email": target, "current_password": "Synthetic-password-42!"},
            ).status_code
            == 302
        )
        links.append(re.search(r"https?://\S+", mail.outbox[-1].body)[0])
    barrier = Barrier(2)
    original_save = User.save

    def simultaneous_save(user, *args, **kwargs):
        if user.email == target and "email" in kwargs.get("update_fields", []):
            barrier.wait(timeout=10)
        return original_save(user, *args, **kwargs)

    monkeypatch.setattr(User, "save", simultaneous_save)

    def confirm(url):
        close_old_connections()
        try:
            return Client().post(url).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, links)) == [200, 409]
    assert User.objects.filter(email=target).count() == 1
    assert User.objects.filter(pending_email=target).count() == 1
