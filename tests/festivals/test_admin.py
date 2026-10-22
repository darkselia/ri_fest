import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from django.contrib.admin.templatetags.admin_list import result_headers
from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from festivals.models import Festival
from festivals.services import (
    publish_festival,
    sync_festival_states,
    unpublish_festival,
)

pytestmark = pytest.mark.django_db


def test_admin_create_and_errors(client, organizer, now):
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_add")
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["adminform"].form["timezone"].value() == "Asia/Krasnoyarsk"
    assert "Актуальный статус" not in response.content.decode()
    assert "Сохранённый статус" not in response.content.decode()
    assert '<select name="timezone"' in response.content.decode()
    assert "UTC+07:00 — Красноярск" in response.content.decode()
    html = response.content.decode()
    assert "Сохранить черновик" in html
    assert "Сохранить и опубликовать" in html
    assert html.index('name="_save"') < html.index('name="_publish"')
    assert 'name="_continue"' not in html
    assert 'name="_addanother"' not in html
    response = client.post(
        url,
        {
            "name": "Синтетический",
            "theme": "Тема",
            "timezone": "invalid",
        },
    )
    assert response.status_code == 200
    assert response.context["adminform"].form["name"].value() == "Синтетический"
    assert not Festival.objects.exists()
    response = client.post(
        url,
        {
            "name": "Черновик",
            "_save": "1",
            "timezone": "Asia/Krasnoyarsk",
            "status": "open",
            "published_at": "2030-01-01",
        },
    )
    assert response.status_code == 302
    festival = Festival.objects.get()
    assert festival.status == "draft"
    assert festival.published_at is None


def test_admin_publish_search_filter_and_no_delete(client, organizer, draft, now):
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_changelist")
    change_url = reverse("admin:festivals_festival_change", args=[draft.pk])
    response = client.get(change_url)
    assert 'name="_publish"' in response.content.decode()
    html = response.content.decode()
    assert "Управление фестивалем" not in html
    assert html.index('class="form-row field-name"') < html.index('name="_save"')
    assert html.index('name="_save"') < html.index('name="_publish"')
    response = client.post(
        change_url,
        {
            "name": draft.name,
            "theme": "Новая тема",
            "start_date": "2030-01-28",
            "end_date": "2030-01-31",
            "timezone": "Asia/Krasnoyarsk",
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    draft.refresh_from_db()
    assert draft.status == "open"
    assert draft.theme == "Новая тема"
    assert 'name="_publish"' not in client.get(change_url).content.decode()
    response = client.get(
        url, {"q": "Синтетический", "status__exact": "open", "o": "2"}
    )
    assert response.status_code == 200
    assert list(response.context["cl"].queryset) == [draft]
    assert 'name="action"' not in response.content.decode()
    assert 'name="_selected_action"' not in response.content.decode()
    assert "Актуальный статус" not in response.content.decode()
    assert (
        client.post(
            reverse("admin:festivals_festival_delete", args=[draft.pk])
        ).status_code
        == 403
    )


@pytest.mark.parametrize("superuser", [False, True])
def test_archive_direct_post_denied_and_restore_confirmed(
    client, organizer, django_user_model, draft, now, superuser
):
    from datetime import datetime, timezone

    actor = organizer
    if superuser:
        actor = django_user_model.objects.create_superuser("root@example.invalid")
    publish_festival(draft.pk, actor=actor)
    sync_festival_states(at=datetime(2031, 1, 1, tzinfo=timezone.utc))
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[draft.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert 'name="_publish"' not in response.content.decode()
    assert client.post(url, {"name": "Hack", "timezone": "UTC"}).status_code == 403
    restore_url = reverse("admin:festivals_festival_restore", args=[draft.pk])
    assert f'href="{restore_url}"' in response.content.decode()
    response = client.get(restore_url)
    assert response.status_code == 200
    assert '<select name="timezone"' in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == "archived"
    response = client.post(
        restore_url,
        {"start_date": "2030-01-28", "end_date": "2030-01-31", "timezone": "UTC"},
    )
    assert response.status_code == 302
    draft.refresh_from_db()
    assert draft.status == "open"


@pytest.mark.parametrize("state", ["draft", "open", "running", "archived", "expired"])
@pytest.mark.parametrize("role", ["organizer", "superuser", "viewer"])
def test_return_links_are_in_history_toolbar_without_duplicates(
    client, organizer, django_user_model, draft, now, monkeypatch, state, role
):
    at = now
    if state != "draft":
        publish_festival(draft.pk, actor=organizer)
        if state == "running":
            at = datetime(2030, 1, 29, tzinfo=timezone.utc)
        elif state in {"archived", "expired"}:
            at = datetime(2030, 2, 1, tzinfo=timezone.utc)
        if state == "archived":
            sync_festival_states(at=at)
    monkeypatch.setattr("festivals.services.timezone.now", lambda: at)
    actor = organizer
    if role == "superuser":
        actor = django_user_model.objects.create_superuser("tools-root@example.invalid")
    elif role == "viewer":
        actor = django_user_model.objects.create_user(
            "tools-viewer@example.invalid", is_staff=True
        )
        actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="festivals", codename="view_festival"
            )
        )
    client.force_login(actor)
    before = Festival.objects.values().get(pk=draft.pk)
    response = client.get(reverse("admin:festivals_festival_change", args=[draft.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    tools = re.search(r'<ul class="object-tools">(.*?)</ul>', html, re.S)
    assert tools is not None
    history = reverse("admin:festivals_festival_history", args=[draft.pk])
    assert f'href="{history}"' in tools[1]

    for action, visible, css_class in [
        (
            "restore",
            role != "viewer" and state in {"archived", "expired"},
            "festival-tool--restore",
        ),
        ("unpublish", role != "viewer" and state != "draft", "festival-tool--draft"),
    ]:
        url = reverse(f"admin:festivals_festival_{action}", args=[draft.pk])
        assert html.count(f'href="{url}"') == int(visible)
        assert (f'href="{url}"' in tools[1]) == visible
        assert (css_class in tools[1]) == visible
    can_publish = state == "draft" and role != "viewer"
    assert "Управление фестивалем" not in html
    assert ('name="_publish"' in html) == can_publish
    assert ("Сохранить черновик" in html) == can_publish
    assert ("Сохранить изменения" in html) == (
        role != "viewer" and state in {"open", "running"}
    )
    assert 'name="_continue"' not in html
    assert 'name="_addanother"' not in html
    assert Festival.objects.values().get(pk=draft.pk) == before


@pytest.mark.parametrize("superuser", [False, True])
@pytest.mark.parametrize("day", [1, 29])
def test_form_opened_before_archive_cannot_save_without_sync(
    client, organizer, django_user_model, draft, now, monkeypatch, superuser, day
):
    actor = organizer
    if superuser:
        actor = django_user_model.objects.create_superuser(
            "archive-root@example.invalid"
        )
    publish_festival(
        draft.pk, actor=actor, at=datetime(2030, 1, day, tzinfo=timezone.utc)
    )
    boundary = datetime(2030, 1, 31, 17, tzinfo=timezone.utc)
    opened_at = boundary - timedelta(microseconds=1)
    monkeypatch.setattr("festivals.services.timezone.now", lambda: opened_at)
    client.force_login(actor)
    url = reverse("admin:festivals_festival_change", args=[draft.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["has_change_permission"]
    before = Festival.objects.values().get(pk=draft.pk)

    monkeypatch.setattr("festivals.services.timezone.now", lambda: boundary)
    response = client.post(
        url,
        {
            "name": "Не должно сохраниться",
            "theme": "Правка открытой формы",
            "start_date": "2031-01-28",
            "end_date": "2031-01-31",
            "timezone": "Etc/GMT+12",
            "_save": "1",
        },
    )
    assert response.status_code == 403
    assert Festival.objects.values().get(pk=draft.pk) == before
    response = client.get(url)
    assert response.status_code == 200
    assert not response.context["has_change_permission"]
    assert response.context["can_restore_festival"]
    assert response.context["can_unpublish_festival"]
    assert Festival.objects.values().get(pk=draft.pk) == before


def test_restore_bad_form_keeps_values(client, organizer, draft, now):
    client.force_login(organizer)
    response = client.post(
        reverse("admin:festivals_festival_restore", args=[draft.pk]),
        {
            "start_date": "2030-02-10",
            "end_date": "2030-01-01",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert response.context["form"]["start_date"].value() == "2030-02-10"


def test_restore_csrf_and_permission(client, organizer, draft, now, django_user_model):
    from django.test import Client

    url = reverse("admin:festivals_festival_restore", args=[draft.pk])
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(organizer)
    assert csrf_client.post(url, {}).status_code == 403
    staff = django_user_model.objects.create_user(
        "staff@example.invalid", is_staff=True
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403


def test_group_has_only_festival_permissions():
    group = Group.objects.get(name="Организаторы")
    assert set(group.permissions.values_list("codename", flat=True)) == {
        "add_festival",
        "view_festival",
        "change_festival",
        "publish_festival",
        "restore_festival",
    }


def test_removed_bulk_publication_does_nothing(client, organizer, draft, now):
    second = Festival.objects.create(
        name="Другой", start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
    )
    client.force_login(organizer)
    response = client.post(
        reverse("admin:festivals_festival_changelist"),
        {
            "action": "publish_selected",
            "_selected_action": [draft.pk, second.pk],
        },
    )
    assert response.status_code == 200
    assert not Festival.objects.exclude(status="draft").exists()


def test_list_has_no_actions_and_sorts_only_by_start_date(
    client, organizer, draft, now
):
    client.force_login(organizer)
    later = Festival.objects.create(
        name="Альфа", start_date=date(2030, 2, 1), end_date=date(2030, 2, 2)
    )
    list_url = reverse("admin:festivals_festival_changelist")
    response = client.get(list_url)
    headers = list(result_headers(response.context["cl"]))
    assert [str(header["text"]) for header in headers if header["sortable"]] == [
        "Дата начала"
    ]
    assert [obj.pk for obj in response.context["cl"].queryset] == [draft.pk, later.pk]
    assert "Действия" not in [str(header["text"]) for header in headers]
    assert "formaction=" not in response.content.decode()
    assert "Актуальный статус" not in response.content.decode()
    response = client.get(list_url, {"o": "-2"})
    assert [obj.pk for obj in response.context["cl"].queryset] == [later.pk, draft.pk]


def test_return_to_draft_requires_csrf_and_permission(
    client, organizer, django_user_model, draft, now
):
    from django.test import Client

    publish_festival(draft.pk, actor=organizer)
    url = reverse("admin:festivals_festival_unpublish", args=[draft.pk])
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(organizer)
    assert csrf_client.post(url, {}).status_code == 403
    viewer = django_user_model.objects.create_user(
        "viewer@example.invalid", is_staff=True
    )
    viewer.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="festivals", codename="view_festival"
        )
    )
    client.force_login(viewer)
    response = client.get(reverse("admin:festivals_festival_change", args=[draft.pk]))
    assert response.status_code == 200
    assert f'href="{url}"' not in response.content.decode()
    assert client.get(url).status_code == 403
    assert client.post(url, {}).status_code == 403
    draft.refresh_from_db()
    assert draft.status == "open"


@pytest.mark.parametrize(
    "at,stored_archive",
    [
        (datetime(2030, 1, 1, tzinfo=timezone.utc), False),
        (datetime(2030, 1, 29, tzinfo=timezone.utc), False),
        (datetime(2030, 2, 1, tzinfo=timezone.utc), True),
        (datetime(2030, 2, 1, tzinfo=timezone.utc), False),
    ],
)
def test_return_to_draft_from_every_state_is_confirmed(
    client, organizer, draft, now, monkeypatch, at, stored_archive
):
    published = publish_festival(draft.pk, actor=organizer)
    if stored_archive:
        sync_festival_states(at=at)
    monkeypatch.setattr("festivals.services.timezone.now", lambda: at)
    client.force_login(organizer)
    change_url = reverse("admin:festivals_festival_change", args=[draft.pk])
    url = reverse("admin:festivals_festival_unpublish", args=[draft.pk])
    response = client.get(change_url)
    assert f'href="{url}"' in response.content.decode()
    draft.refresh_from_db()
    original_status = draft.status
    response = client.get(url)
    assert response.status_code == 200
    assert "Подтвердить возврат в черновик" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == original_status
    response = client.post(url, {})
    assert response.status_code == 302
    assert response.url == change_url
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.published_at == published.published_at
    assert draft.start_date == published.start_date
    assert draft.end_date == published.end_date
    assert draft.timezone == published.timezone
    assert sync_festival_states(at=at) == 0
    assert 'name="_publish"' in client.get(change_url).content.decode()


@pytest.mark.parametrize("create", [False, True])
def test_publish_invalid_form_keeps_values(client, organizer, draft, now, create):
    client.force_login(organizer)
    url = (
        reverse("admin:festivals_festival_add")
        if create
        else reverse("admin:festivals_festival_change", args=[draft.pk])
    )
    response = client.post(
        url,
        {
            "name": "Исправленное название",
            "start_date": "2030-02-10",
            "end_date": "2030-01-01",
            "timezone": "UTC",
            "_publish": "1",
        },
    )
    assert response.status_code == 200
    form = response.context["adminform"].form
    assert form.errors
    assert form["name"].value() == "Исправленное название"
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.name == "Синтетический фестиваль"
    assert Festival.objects.count() == 1


@pytest.mark.parametrize("conflict", [False, True])
def test_publish_business_error_keeps_draft(client, organizer, draft, now, conflict):
    if conflict:
        other = Festival.objects.create(
            name="Другой", start_date=draft.start_date, end_date=draft.end_date
        )
        publish_festival(other.pk, actor=organizer)
    client.force_login(organizer)
    response = client.post(
        reverse("admin:festivals_festival_change", args=[draft.pk]),
        {
            "name": draft.name,
            "start_date": "2030-01-28" if conflict else "",
            "end_date": "2030-01-31" if conflict else "",
            "timezone": "UTC",
            "_publish": "1",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert any(message.level == 40 for message in response.context["messages"])
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.published_at is None


@pytest.mark.parametrize("create", [False, True])
def test_publish_csrf_and_permission(
    client, django_user_model, organizer, draft, now, create
):
    from django.test import Client

    url = (
        reverse("admin:festivals_festival_add")
        if create
        else reverse("admin:festivals_festival_change", args=[draft.pk])
    )
    data = {
        "name": "Не должно сохраниться",
        "start_date": "2030-01-28",
        "end_date": "2030-01-31",
        "timezone": "UTC",
        "_publish": "1",
    }
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(organizer)
    assert csrf_client.post(url, data).status_code == 403
    staff = django_user_model.objects.create_user(
        "editor@example.invalid", is_staff=True
    )
    staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="festivals", codename="add_festival"
        ),
        Permission.objects.get(
            content_type__app_label="festivals", codename="change_festival"
        ),
    )
    client.force_login(staff)
    assert 'name="_publish"' not in client.get(url).content.decode()
    assert client.post(url, data).status_code == 403
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.name == "Синтетический фестиваль"
    assert Festival.objects.count() == 1


@pytest.mark.parametrize("day,expected", [(1, "open"), (29, "running")])
def test_create_and_publish_in_one_post(
    client, organizer, now, monkeypatch, day, expected
):
    at = datetime(2030, 1, day, tzinfo=timezone.utc)
    monkeypatch.setattr("festivals.services.timezone.now", lambda: at)
    client.force_login(organizer)
    response = client.post(
        reverse("admin:festivals_festival_add"),
        {
            "name": "Публикация при создании",
            "theme": "Сохранённая тема",
            "start_date": "2030-01-28",
            "end_date": "2030-01-31",
            "timezone": "Asia/Krasnoyarsk",
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    festival = Festival.objects.get()
    assert festival.status == expected
    assert festival.theme == "Сохранённая тема"
    assert festival.start_date == date(2030, 1, 28)
    assert festival.end_date == date(2030, 1, 31)
    assert festival.timezone == "Asia/Krasnoyarsk"
    assert festival.published_at == at
    assert response.url == reverse(
        "admin:festivals_festival_change", args=[festival.pk]
    )
    assert "Сохранить изменения" in client.get(response.url).content.decode()


@pytest.mark.parametrize("reason", ["missing_dates", "past", "conflict"])
def test_failed_first_publication_saves_one_draft(client, organizer, now, reason):
    if reason == "conflict":
        other = Festival.objects.create(
            name="Текущий", start_date=date(2030, 1, 28), end_date=date(2030, 1, 31)
        )
        publish_festival(other.pk, actor=organizer)
    data = {
        "name": "Черновик после ошибки публикации",
        "theme": "Сохранённые данные",
        "start_date": "2030-01-28",
        "end_date": "2030-01-31",
        "timezone": "Asia/Krasnoyarsk",
        "_publish": "1",
    }
    if reason == "missing_dates":
        data.update(start_date="", end_date="")
    elif reason == "past":
        data.update(start_date="2029-01-28", end_date="2029-01-31")
    client.force_login(organizer)
    response = client.post(reverse("admin:festivals_festival_add"), data, follow=True)
    assert response.status_code == 200
    assert any(
        message.level == 40 and "Черновик сохранён" in str(message)
        for message in response.context["messages"]
    )
    festival = Festival.objects.get(name=data["name"])
    change_url = reverse("admin:festivals_festival_change", args=[festival.pk])
    assert response.redirect_chain == [(change_url, 302)]
    assert festival.status == "draft"
    assert festival.published_at is None
    assert festival.theme == data["theme"]
    assert response.context["adminform"].form["theme"].value() == data["theme"]
    count = Festival.objects.count()

    # Retry from the redirected card, rather than creating another festival.
    data.update(start_date="2030-01-28", end_date="2030-01-31")
    response = client.post(change_url, data)
    assert response.status_code == 302
    assert Festival.objects.count() == count
    festival.refresh_from_db()
    assert festival.status == ("draft" if reason == "conflict" else "open")


def test_save_draft_and_republish_keep_first_publication(client, organizer, draft, now):
    published = publish_festival(draft.pk, actor=organizer)
    unpublish_festival(draft.pk, actor=organizer)
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_change", args=[draft.pk])
    data = {
        "name": draft.name,
        "theme": "Сохранённый черновик",
        "start_date": "2030-01-28",
        "end_date": "2030-01-31",
        "timezone": "Asia/Krasnoyarsk",
        "_save": "1",
    }
    assert client.post(url, data).status_code == 302
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.theme == data["theme"]
    assert draft.published_at == published.published_at
    data.pop("_save")
    data["_publish"] = "1"
    assert client.post(url, data).status_code == 302
    draft.refresh_from_db()
    assert draft.status == "open"
    assert draft.published_at == published.published_at


def test_festival_submit_buttons_do_not_replace_user_admin(client, django_user_model):
    actor = django_user_model.objects.create_superuser("users-admin@example.invalid")
    client.force_login(actor)
    response = client.get(reverse("admin:accounts_user_add"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Сохранить черновик" not in html
    assert 'name="_publish"' not in html
    assert 'name="_continue"' in html
    assert 'name="_addanother"' in html


def test_timezone_negative_offset_selection(client, organizer, now):
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_add")
    response = client.get(url)
    choices = dict(response.context["adminform"].form.fields["timezone"].choices)
    assert len(choices) == 27
    assert choices["Etc/GMT+12"] == "UTC-12:00"
    assert choices["Etc/GMT-14"] == "UTC+14:00"
    assert choices["UTC"] == "UTC+00:00"
    assert choices["Etc/GMT+5"] == "UTC-05:00"
    assert choices["Etc/GMT-3"] == "UTC+03:00"
    assert [label for label in choices.values() if " — " in label] == [
        "UTC+07:00 — Красноярск"
    ]
    response = client.post(url, {"name": "Западный пояс", "timezone": "Etc/GMT+5"})
    assert response.status_code == 302
    assert Festival.objects.get().timezone == "Etc/GMT+5"


@pytest.mark.parametrize(
    "zone,label",
    [
        ("Asia/Kathmandu", "UTC+05:45"),
        ("Europe/Moscow", "UTC+03:00"),
        ("America/New_York", "UTC-05:00"),
    ],
)
def test_existing_timezone_is_preserved(client, organizer, draft, now, zone, label):
    draft.timezone = zone
    draft.save(update_fields=["timezone"])
    client.force_login(organizer)
    url = reverse("admin:festivals_festival_change", args=[draft.pk])
    response = client.get(url)
    form = response.context["adminform"].form
    assert form["timezone"].value() == zone
    assert dict(form.fields["timezone"].choices)[zone] == label
    response = client.post(
        url,
        {
            "name": draft.name,
            "timezone": zone,
            "start_date": "2030-01-28",
            "end_date": "2030-01-31",
        },
    )
    assert response.status_code == 302
    draft.refresh_from_db()
    assert draft.timezone == zone


@pytest.mark.parametrize(
    "direction,description",
    [
        ("ahead", "опережает его"),
        ("behind", "отстаёт от него"),
    ],
)
def test_admin_timezone_hint_is_russian(client, organizer, direction, description):
    client.force_login(organizer)
    response = client.get(reverse("admin:jsi18n"))
    assert response.status_code == 200
    catalog = json.loads(
        re.search(
            r"const newcatalog = (\{.*?\});",
            response.content.decode(),
            re.S,
        ).group(1)
    )
    key = (
        "Note: Enter times in the %(timezone)s timezone. "
        f"(You are %(offset)s hour {direction}.)"
    )
    for form, suffix in zip(
        catalog[key], ("час", "часа", "часов", "часа"), strict=True
    ):
        rendered = form % {"timezone": "UTC", "offset": 7}
        assert rendered.startswith("Примечание: вводите время")
        assert description in rendered
        assert rendered.endswith(f"7 {suffix}.")


def test_admin_javascript_catalog_requires_staff(client):
    assert client.get(reverse("admin:jsi18n")).status_code == 302
