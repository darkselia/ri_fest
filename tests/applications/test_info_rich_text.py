from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.template.loader import render_to_string

from applications.forms import TemplatePreviewForm
from applications.models import FormField
from applications.schema import export_schema, prepare_schema
from festivals.models import Festival


@pytest.mark.django_db
def test_info_migration_preserves_text_links_and_escapes_html():
    festival = Festival.objects.create(name="Тест")
    field = FormField.objects.create(
        festival=festival,
        field_type="info",
        label="Информация",
        help_text="Первая строка\nhttps://example.com <script>alert(1)</script>",
    )
    migration = import_module("applications.migrations.0018_info_rich_text")
    migration.convert_info_text(apps, SimpleNamespace(connection=connection))
    field.refresh_from_db()
    assert "<br" in field.help_text
    assert 'href="https://example.com"' in field.help_text
    assert "<script>" not in field.help_text
    assert "&lt;script&gt;" in field.help_text


@pytest.mark.django_db
def test_info_html_is_sanitized_on_save_and_in_unsaved_preview():
    festival = Festival.objects.create(name="Тест")
    FormField.objects.create(festival=festival, field_type="info", label="Информация")
    rows = export_schema(festival)
    rows[0]["help_text"] = (
        "<strong>Важно</strong><script>alert(1)</script>"
        '<a href="javascript:alert(1)" onclick="alert(1)">ссылка</a>'
    )
    _, normalized = prepare_schema(festival, rows)
    assert "<strong>Важно</strong>" in normalized[0]["help_text"]
    assert "<script>" not in normalized[0]["help_text"]
    assert "javascript:" not in normalized[0]["help_text"]
    preview = TemplatePreviewForm(rows, public=True)
    html = render_to_string(
        "components/forms/application_fields.html", {"preview_form": preview}
    )
    assert "<strong>Важно</strong>" in html
    assert "<script>" not in html
    assert "onclick=" not in html
