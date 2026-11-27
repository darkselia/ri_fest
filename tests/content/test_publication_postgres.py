import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.test import Client
from django.urls import reverse

from content.models import Page, PageVersion


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_parallel_publication_cannot_claim_same_address(django_user_model):
    if connection.vendor != "postgresql":
        pytest.skip("Requires isolated PostgreSQL")
    Page.objects.get_or_create(is_home=True, defaults={"slug": "home"})
    user = django_user_model.objects.create_superuser(
        email="pages-race@example.invalid", password="example-password"
    )
    pages = []
    for slug in ("first", "second"):
        page = Page.objects.create(slug=slug)
        PageVersion.objects.create(page=page, state="draft", title=slug)
        pages.append(page)
    barrier = Barrier(2)

    def publish(page):
        close_old_connections()
        client = Client()
        client.force_login(django_user_model.objects.get(pk=user.pk))
        barrier.wait(timeout=10)
        try:
            return client.post(
                reverse("admin:content_page_change", args=[page.pk]),
                {
                    "slug": "shared",
                    "title": page.slug,
                    "version_revision": 0,
                    "_publish": "1",
                },
            ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(publish, pages))
    assert sorted(statuses) == [200, 302]
    assert Page.objects.filter(published_slug="shared").count() == 1
    assert PageVersion.objects.filter(state="published", page__in=pages).count() == 1


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_parallel_block_publication_rejects_stale_version(django_user_model):
    if connection.vendor != "postgresql":
        pytest.skip("Requires isolated PostgreSQL")
    Page.objects.get_or_create(is_home=True, defaults={"slug": "home"})
    user = django_user_model.objects.create_superuser(
        email="blocks-race@example.invalid", password="example-password"
    )
    page = Page.objects.create(slug="blocks-race")
    PageVersion.objects.create(page=page, state="draft", title="Тест")
    barrier = Barrier(2)

    def publish(text):
        close_old_connections()
        client = Client()
        client.force_login(django_user_model.objects.get(pk=user.pk))
        barrier.wait(timeout=10)
        try:
            return client.post(
                reverse("admin:content_page_change", args=[page.pk]),
                {
                    "slug": page.slug,
                    "title": text,
                    "version_revision": 0,
                    "blocks": json.dumps(
                        [{"kind": "text", "text": text, "visible": True}]
                    ),
                    "_publish": "1",
                },
            ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(publish, ["Первый", "Второй"]))
    assert sorted(statuses) == [200, 302]
    draft = page.versions.get(state="draft")
    published = page.versions.get(state="published")
    assert (
        draft.title
        == published.title
        == draft.blocks.get().text
        == published.blocks.get().text
    )
