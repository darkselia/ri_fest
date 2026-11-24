import pytest
from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from content.models import Page, PageVersion


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
