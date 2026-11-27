import importlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from content.models import ContentFile, Page, PageBlock, PageVersion
from festivals.models import Festival


@pytest.mark.django_db
def test_page_list_search_and_sort_by_displayed_title_and_address(client, admin_user):
    first = Page.objects.create(slug="z-draft", published_slug="a-public")
    second = Page.objects.create(slug="b-draft")
    PageVersion.objects.create(page=first, state="draft", title="Альфа")
    PageVersion.objects.create(page=first, state="published", title="Старое имя")
    PageVersion.objects.create(page=second, state="draft", title="Бета")
    client.force_login(admin_user)
    url = reverse("admin:content_page_changelist")

    def ids(params):
        return [
            page.pk
            for page in client.get(url, params).context["cl"].result_list
            if page.pk in (first.pk, second.pk)
        ]

    assert ids({"q": "Альфа"}) == [first.pk]
    assert ids({"q": "/info/a-public/"}) == [first.pk]
    assert ids({"q": "Старое имя"}) == []
    assert ids({"o": "0"}) == [first.pk, second.pk]
    assert ids({"o": "-0"}) == [second.pk, first.pk]
    assert ids({"o": "1"}) == [first.pk, second.pk]
    assert ids({"o": "2"}) == [second.pk, first.pk]


@pytest.mark.django_db
def test_markdown_migration_converts_both_page_versions():
    page = Page.objects.create(slug="migration-example")
    for state in ("draft", "published"):
        version = PageVersion.objects.create(page=page, state=state, title="Тест")
        PageBlock.objects.create(
            version=version,
            position=0,
            kind="text",
            text="**Текст** [ссылка](https://example.com)",
        )

    migration = importlib.import_module(
        "content.migrations.0005_convert_markdown_to_html"
    )
    migration.convert_text_blocks(apps, SimpleNamespace(connection=connection))

    for block in PageBlock.objects.filter(version__page=page):
        assert "<strong>Текст</strong>" in block.text
        assert '<a href="https://example.com">ссылка</a>' in block.text


@pytest.mark.django_db
def test_page_blocks_preserve_published_version_and_sanitize_html(client, organizer):
    client.force_login(organizer)
    preview = client.post(
        reverse("admin:content_page_preview"),
        {
            "title": "Предпросмотр",
            "blocks": json.dumps(
                [{"kind": "text", "text": "<strong>Без сохранения</strong>"}]
            ),
        },
    )
    assert "<strong>Без сохранения</strong>" in preview.content.decode()
    assert not Page.objects.filter(slug="blocks").exists()
    url = reverse("admin:content_page_add")
    blocks = [
        {
            "kind": "text",
            "title": "Блок",
            "visible": True,
            "text": '<strong>Текст</strong> <script>alert(1)</script><a href="javascript:alert(1)" onclick="alert(2)">ссылка</a>',
        }
    ]
    response = client.post(
        url,
        {
            "slug": "blocks",
            "title": "Страница",
            "blocks": json.dumps(blocks),
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    page = Page.objects.get(slug="blocks")
    html = client.get(reverse("content_page", args=["blocks"])).content.decode()
    assert "<strong>Текст</strong>" in html
    assert "<script>alert(1)</script>" not in html
    assert "javascript:alert" not in html
    assert "onclick=" not in html
    blocks[0]["text"] = "Новый текст"
    change = reverse("admin:content_page_change", args=[page.pk])
    response = client.post(
        change,
        {
            "slug": "blocks",
            "title": "Страница",
            "version_revision": page.revision,
            "blocks": json.dumps(blocks),
            "_save": "1",
        },
    )
    assert response.status_code == 302
    assert (
        "Новый текст"
        not in client.get(reverse("content_page", args=["blocks"])).content.decode()
    )
    page.refresh_from_db()
    assert "Есть неопубликованные изменения" in client.get(change).content.decode()
    response = client.post(
        change, {"version_revision": page.revision, "_discard_changes": "1"}
    )
    assert response.status_code == 302
    restored = page.versions.get(state="draft").blocks.get().text
    assert "<strong>Текст</strong>" in restored
    assert "<script>" not in restored
    assert "javascript:" not in restored


@pytest.mark.django_db
def test_draft_save_stays_in_editor_and_preview_uses_draft_blocks(client, organizer):
    client.force_login(organizer)
    blocks = [{"kind": "text", "text": "Текст черновика", "visible": True}]
    response = client.post(
        reverse("admin:content_page_add"),
        {
            "slug": "draft-preview",
            "title": "Черновик",
            "blocks": json.dumps(blocks),
            "_save": "1",
        },
    )
    page = Page.objects.get(slug="draft-preview")
    change = reverse("admin:content_page_change", args=[page.pk])
    assert response.url == change
    editor_response = client.get(change)
    assert b"data-page-text-widget" in editor_response.content
    assert b"<trix-editor" in editor_response.content
    form = editor_response.context["adminform"].form
    response = client.post(
        reverse("admin:content_page_preview"),
        {"title": "Черновик", "blocks": form["blocks"].value()},
    )
    assert "Текст черновика" in response.content.decode()
    assert not page.versions.filter(state="published").exists()
    response = client.post(
        change,
        {
            "slug": page.slug,
            "title": "Черновик",
            "blocks": form["blocks"].value(),
            "version_revision": page.revision,
            "_save": "1",
        },
    )
    assert response.url == change


@pytest.mark.django_db
def test_published_file_access_follows_page_and_file_is_protected(
    client, organizer, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    asset = ContentFile.objects.create(
        name="Правила",
        original_name="rules.txt",
        file=SimpleUploadedFile("rules.txt", b"rules"),
    )
    blocks = [{"kind": "file", "title": "", "visible": True, "file_id": asset.pk}]
    client.force_login(organizer)
    organizer.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="content", codename="view_contentfile"
        )
    )
    organizer = type(organizer).objects.get(pk=organizer.pk)
    client.force_login(organizer)
    response = client.post(
        reverse("admin:content_page_add"),
        {
            "slug": "rules-file",
            "title": "Правила",
            "login_required": "on",
            "blocks": json.dumps(blocks),
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    page = Page.objects.get(slug="rules-file")
    block = page.versions.get(state="published").blocks.get()
    file_url = reverse("content_page_file", args=[page.pk, block.pk])
    assert client.get(file_url).status_code == 200
    with pytest.raises(ProtectedError):
        asset.delete()
    response = client.post(
        reverse("admin:content_page_change", args=[page.pk]),
        {
            "slug": page.slug,
            "title": "Правила",
            "version_revision": page.revision,
            "blocks": "[]",
            "_save": "1",
        },
    )
    assert response.status_code == 302
    page.refresh_from_db()
    with pytest.raises(ProtectedError):
        asset.delete()
    client.logout()
    assert client.get(file_url).status_code == 302
    assert (
        client.get(
            reverse("content_page_file", args=[page.pk, block.pk + 10000])
        ).status_code
        == 404
    )
    client.force_login(organizer)
    response = client.post(
        reverse("admin:content_page_change", args=[page.pk]),
        {"version_revision": page.revision, "_unpublish": "1"},
    )
    assert response.status_code == 302
    assert client.get(file_url).status_code == 404
    asset.delete()


@pytest.mark.django_db
def test_festival_card_displays_only_active_count_and_hides_drafts(client, organizer):
    today = timezone.now().date()
    festival = Festival.objects.create(
        name="Тест фестиваля",
        status="open",
        published_at=timezone.now(),
        start_date=today + timedelta(days=30),
        end_date=today + timedelta(days=31),
    )
    Application.objects.create(owner=organizer, festival=festival, status="active")
    client.force_login(organizer)
    response = client.post(
        reverse("admin:content_page_add"),
        {
            "slug": "event-card",
            "title": "Фестиваль",
            "blocks": json.dumps([{"kind": "festival", "festival_id": festival.pk}]),
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    url = reverse("content_page", args=["event-card"])
    assert "Активных анкет: 1" in client.get(url).content.decode()
    festival.status = "archived"
    festival.save(update_fields=["status"])
    html = client.get(url).content.decode()
    assert "Фестиваль завершён" in html
    assert "Активных анкет: 0" in html
    festival.status = "draft"
    festival.save(update_fields=["status"])
    assert "Тест фестиваля" not in client.get(url).content.decode()


@pytest.mark.django_db
def test_copy_blocks_requires_page_change_permission(
    client, organizer, django_user_model
):
    page = Page.objects.create(slug="copy-source")
    draft = PageVersion.objects.create(page=page, state="draft", title="Источник")
    PageBlock.objects.create(version=draft, position=0, kind="text", text="Содержимое")
    url = reverse("admin:content_page_copy_blocks", args=[page.pk])
    staff = django_user_model.objects.create_user(
        email="page-no-access@example.invalid", is_staff=True
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403
    client.force_login(organizer)
    assert client.get(url).json()["blocks"][0]["text"] == "Содержимое"
    assert draft.blocks.count() == 1


@pytest.mark.django_db
def test_invalid_block_reports_position_without_saving(client, organizer):
    client.force_login(organizer)
    blocks = [{"kind": "text", "text": "Хорошо"}, {"kind": "image", "file_id": None}]
    response = client.post(
        reverse("admin:content_page_add"),
        {
            "slug": "invalid-blocks",
            "title": "Тест",
            "blocks": json.dumps(blocks),
            "_save": "1",
        },
    )
    assert response.status_code == 200
    assert "Блок 2." in response.content.decode()
    assert not Page.objects.filter(slug="invalid-blocks").exists()


@pytest.fixture(autouse=True)
def site_setup(db):
    Page.objects.get_or_create(is_home=True, defaults={"slug": "home"})
    group, _ = Group.objects.get_or_create(name="Организаторы")
    group.permissions.add(
        *Permission.objects.filter(
            content_type__app_label="content", content_type__model="page"
        )
    )


@pytest.fixture
def organizer(db, django_user_model):
    user = django_user_model.objects.create_user(
        email="content-organizer@example.invalid", password="example-password"
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    user.groups.add(Group.objects.get(name="Организаторы"))
    return user


@pytest.mark.django_db
def test_publication_keeps_draft_private_and_requires_login(client, organizer):
    client.force_login(organizer)
    add = reverse("admin:content_page_add")
    response = client.post(
        add,
        {"slug": "lodging", "title": "Размещение", "_save": "Сохранить"},
    )
    assert response.status_code == 302
    page = Page.objects.get(slug="lodging")
    url = reverse("content_page", args=["lodging"])
    assert client.get(url).status_code == 404
    change = reverse("admin:content_page_change", args=[page.pk])
    response = client.post(
        change,
        {
            "title": "Для участников",
            "slug": "lodging",
            "login_required": "on",
            "version_revision": page.revision,
            "_publish": "Опубликовать",
        },
    )
    assert response.status_code == 302
    page.refresh_from_db()
    response = client.post(
        change,
        {
            "title": "Новый черновик",
            "slug": "lodging",
            "version_revision": page.revision,
            "_save": "Сохранить",
        },
    )
    assert response.status_code == 302
    client.logout()
    assert client.get(url).status_code == 302
    client.force_login(organizer)
    assert "Для участников" in client.get(url).content.decode()
    assert "Новый черновик" not in client.get(url).content.decode()


@pytest.mark.django_db
def test_stale_save_does_not_replace_draft_or_published(client, organizer):
    page = Page.objects.create(slug="rules")
    PageVersion.objects.create(page=page, state="draft", title="Правила")
    client.force_login(organizer)
    change = reverse("admin:content_page_change", args=[page.pk])
    response = client.post(
        change,
        {
            "title": "Первая правка",
            "slug": "rules",
            "version_revision": 0,
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    response = client.post(
        change,
        {
            "title": "Устаревшая правка",
            "slug": "rules",
            "version_revision": 0,
            "_publish": "1",
        },
    )
    assert response.status_code == 200
    assert "Страница уже изменена" in response.content.decode()
    assert set(page.versions.values_list("title", flat=True)) == {"Первая правка"}


@pytest.mark.django_db
def test_unpublished_home_remains_available(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert "Сайт готовится к запуску" in response.content.decode()


@pytest.mark.django_db
def test_staff_without_page_permission_cannot_publish(client, django_user_model):
    user = django_user_model.objects.create_user(
        email="other-staff@example.invalid",
        password="example-password",
        is_staff=True,
    )
    page = Page.objects.create(slug="private")
    PageVersion.objects.create(page=page, state="draft", title="Черновик")
    client.force_login(user)
    response = client.post(
        reverse("admin:content_page_change", args=[page.pk]),
        {"title": "Чужая правка", "version_revision": 0, "_publish": "1"},
    )
    assert response.status_code == 403
    assert not page.versions.filter(state="published").exists()


@pytest.mark.django_db
def test_home_cannot_be_deleted_through_admin(client, organizer):
    client.force_login(organizer)
    page = Page.objects.get(is_home=True)
    response = client.post(reverse("admin:content_page_delete", args=[page.pk]))
    assert response.status_code == 403
    assert Page.objects.filter(pk=page.pk).exists()


@pytest.mark.django_db
def test_edit_address_and_discard_then_publish_and_unpublish(client, organizer):
    client.force_login(organizer)
    response = client.post(
        reverse("admin:content_page_add"),
        {"slug": "before", "title": "До", "_publish": "1"},
    )
    assert response.status_code == 302
    page = Page.objects.get(slug="before")
    change = reverse("admin:content_page_change", args=[page.pk])
    old_url = reverse("content_page", args=["before"])
    new_url = reverse("content_page", args=["after"])
    assert "До" in client.get(old_url).content.decode()

    response = client.post(
        change,
        {
            "slug": "after",
            "title": "После",
            "login_required": "on",
            "version_revision": page.revision,
            "_save": "1",
        },
    )
    assert response.status_code == 302
    page.refresh_from_db()
    assert "До" in client.get(old_url).content.decode()
    assert client.get(new_url).status_code == 404
    assert "Есть неопубликованные изменения" in client.get(change).content.decode()
    listing = client.get(reverse("admin:content_page_changelist")).content.decode()
    assert "/info/before/" in listing
    assert "/info/after/" not in listing

    response = client.post(
        change, {"version_revision": page.revision, "_discard_changes": "1"}
    )
    assert response.status_code == 302
    page.refresh_from_db()
    assert page.slug == "before"
    assert page.versions.get(state="draft").title == "До"
    assert not page.versions.get(state="draft").login_required

    response = client.post(
        change,
        {
            "slug": "after",
            "title": "После",
            "login_required": "on",
            "version_revision": page.revision,
            "_publish": "1",
        },
    )
    assert response.status_code == 302
    page.refresh_from_db()
    assert client.get(old_url).status_code == 404
    assert "После" in client.get(new_url).content.decode()
    client.logout()
    assert client.get(new_url).status_code == 302
    client.force_login(organizer)
    response = client.post(
        change, {"version_revision": page.revision, "_unpublish": "1"}
    )
    assert response.status_code == 302
    assert client.get(new_url).status_code == 404
    assert page.versions.get(state="draft").title == "После"


@pytest.mark.django_db
def test_published_address_cannot_be_taken_by_another_draft(client, organizer):
    client.force_login(organizer)
    add = reverse("admin:content_page_add")
    client.post(add, {"slug": "in-use", "title": "Первая", "_publish": "1"})
    page = Page.objects.get(slug="in-use")
    client.post(
        reverse("admin:content_page_change", args=[page.pk]),
        {"slug": "changed", "title": "Первая", "version_revision": page.revision},
    )
    response = client.post(add, {"slug": "in-use", "title": "Вторая", "_publish": "1"})
    assert response.status_code == 200
    assert (
        "Этот адрес используется опубликованной страницей" in response.content.decode()
    )
    assert Page.objects.count() == 2


@pytest.mark.django_db
def test_stale_unpublish_does_not_hide_page(client, organizer):
    client.force_login(organizer)
    client.post(
        reverse("admin:content_page_add"),
        {"slug": "stable", "title": "Страница", "_publish": "1"},
    )
    page = Page.objects.get(slug="stable")
    response = client.post(
        reverse("admin:content_page_change", args=[page.pk]),
        {"version_revision": page.revision - 1, "_unpublish": "1"},
    )
    assert response.status_code == 302
    assert client.get(reverse("content_page", args=["stable"])).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["_unpublish", "_discard_changes"])
def test_publication_actions_require_permission_and_csrf(
    client, organizer, django_user_model, action
):
    client.force_login(organizer)
    client.post(
        reverse("admin:content_page_add"),
        {"slug": "protected", "title": "Защита", "_publish": "1"},
    )
    page = Page.objects.get(slug="protected")
    url = reverse("admin:content_page_change", args=[page.pk])
    csrf_client = client.__class__(enforce_csrf_checks=True)
    csrf_client.force_login(organizer)
    assert (
        csrf_client.post(
            url, {"version_revision": page.revision, action: "1"}
        ).status_code
        == 403
    )

    staff = django_user_model.objects.create_user(
        email="other@example.invalid", password="example-password", is_staff=True
    )
    client.force_login(staff)
    assert (
        client.post(url, {"version_revision": page.revision, action: "1"}).status_code
        == 403
    )
    assert PageVersion.objects.filter(page=page, state="published").exists()


@pytest.mark.django_db
def test_publication_actions_reflect_saved_changes(client, organizer):
    client.force_login(organizer)
    client.post(
        reverse("admin:content_page_add"),
        {"slug": "example", "title": "Пример", "_publish": "1"},
    )
    page = Page.objects.get(slug="example")
    url = reverse("admin:content_page_change", args=[page.pk])
    html = client.get(url).content.decode()
    assert 'data-has-saved-changes="false"' in html
    assert 'name="_unpublish"' in html
    assert html.index('name="_unpublish"') < html.index('id="page_form"')
    assert "Сохранить без публикации" in html

    client.post(
        url,
        {
            "slug": page.slug,
            "title": "Изменённый пример",
            "version_revision": page.revision,
            "_save": "1",
        },
    )
    assert 'data-has-saved-changes="true"' in client.get(url).content.decode()

    page.refresh_from_db()
    client.post(url, {"version_revision": page.revision, "_discard_changes": "1"})
    assert 'data-has-saved-changes="false"' in client.get(url).content.decode()
