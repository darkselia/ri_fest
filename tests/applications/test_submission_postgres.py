from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection

from accounts.services import delete_account
from applications.models import Application
from applications.schema import schema_revision
from applications.services import delete_application, save_application
from festivals.models import Festival
from festivals.services import (
    archive_festival,
    delete_festival,
    festival_action_preview,
)

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("operation", ["archive", "festival", "account", "draft"])
def test_submit_races_are_atomic(cabinet, django_user_model, operation):
    if connection.vendor != "postgresql":
        pytest.skip("Requires isolated PostgreSQL")
    owner, _, festival, field, price, option = cabinet
    admin = django_user_model.objects.create_superuser(
        "race-cabinet-admin@example.invalid"
    )
    app = Application.objects.create(owner=owner, festival=festival)
    revision = schema_revision(festival)
    barrier = Barrier(2)

    def run(action):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            action()
            return "ok"
        except (
            PermissionDenied,
            ValidationError,
            Application.DoesNotExist,
            Festival.DoesNotExist,
        ):
            return "rejected"
        finally:
            close_old_connections()

    def submit():
        save_application(
            actor=owner,
            application_id=app.pk,
            data={
                f"field_{field.pk}": "Участник",
                f"field_{price.pk}": [str(option.pk)],
            },
            submit=True,
            revision=revision,
        )

    def change():
        if operation == "account":
            delete_account(owner.pk, actor=owner, password="synthetic-password")
        elif operation == "draft":
            delete_application(app.pk, actor=owner)
        else:
            action = "archive" if operation == "archive" else "delete"
            token = festival_action_preview(festival.pk, actor=admin, action=action)[
                "confirmation"
            ]
            function = archive_festival if operation == "archive" else delete_festival
            function(festival.pk, actor=admin, confirmation=token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(run, submit)
        changing = pool.submit(run, change)
        outcomes = [sending.result(timeout=20), changing.result(timeout=20)]
    assert "ok" in outcomes
    remaining = Application.objects.filter(pk=app.pk).first()
    if operation == "account":
        assert not django_user_model.objects.filter(pk=owner.pk).exists()
        if remaining:
            assert remaining.owner_id is None and not remaining.answers.exists()
    elif (
        operation == "archive"
        and Festival.objects.get(pk=festival.pk).status == "archived"
    ):
        assert remaining.status in ("draft", "archived")
    if remaining and remaining.status == "active":
        assert remaining.answers.get().value == "Участник"
        assert remaining.service_selections.get().option_id == option.pk
