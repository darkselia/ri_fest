from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from content.admin import ContentFileForm
from content.models import ContentFile
from content.uploads import MAX_CONTENT_FILE_SIZE


def upload(name, data):
    return SimpleUploadedFile(name, data)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("document.exe", b"file"),
        ("document.exe.pdf", b"file"),
        ("document.pdf", b"MZ executable"),
        ("image.png", b"not a png"),
        ("table.xlsx", b"not an xlsx"),
        ("payload.xyz", b"unknown"),
        ("drawing.svg", b"<svg></svg>"),
        ("image.gif", b"not a gif"),
    ],
)
def test_rejects_executables_and_invalid_preview_formats(name, data):
    form = ContentFileForm({"name": "Пример"}, {"file": upload(name, data)})
    assert not form.is_valid()
    assert "file" in form.errors


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("image.gif", b"GIF89a"),
        ("video.mp4", b"video"),
        ("audio.mp3", b"audio"),
        ("document.pdf", b"document"),
        ("archive.zip", b"archive"),
    ],
)
def test_accepts_allowed_types_and_uses_filename_when_name_is_blank(name, data):
    form = ContentFileForm({"name": ""}, {"file": upload(name, data)})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["name"] == name


def test_keeps_entered_name():
    form = ContentFileForm(
        {"name": "Правила"}, {"file": upload("rules.pdf", b"document")}
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["name"] == "Правила"


def test_rejects_oversized_file():
    file = upload("document.pdf", b"data")
    file.size = MAX_CONTENT_FILE_SIZE + 1
    form = ContentFileForm({"name": "Пример"}, {"file": file})
    assert not form.is_valid()
    assert "20 МБ" in form.errors["file"][0]


def test_accepts_workbook():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as book:
        book.writestr("[Content_Types].xml", "")
        book.writestr("xl/workbook.xml", "")
    form = ContentFileForm(
        {"name": "Расписание"}, {"file": upload("schedule.xlsx", buffer.getvalue())}
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_organizers_receive_file_permissions():
    group = Group.objects.get(name="Организаторы")
    assert set(
        group.permissions.filter(
            content_type__app_label="content", content_type__model="contentfile"
        ).values_list("codename", flat=True)
    ) == {
        "add_contentfile",
        "change_contentfile",
        "delete_contentfile",
        "view_contentfile",
    }


@pytest.mark.django_db
def test_download_requires_permission_and_preserves_original_filename(
    client, admin_user, django_user_model, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    asset = ContentFile.objects.create(
        name="Документ",
        original_name="Правила.pdf",
        file=upload("rules.pdf", b"sample document"),
    )
    url = reverse("admin:content_contentfile_download", args=[asset.pk])
    assert client.get(url).status_code == 302
    staff = django_user_model.objects.create_user(
        email="no-download@example.invalid", password="example-password", is_staff=True
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"] == (
        "attachment; filename*=utf-8''%D0%9F%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%D0%B0.pdf"
    )
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content) == b"sample document"
    assert client.post(url).status_code == 405
    asset.file.delete(save=False)
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_image_preview_requires_file_permission_and_never_serves_other_types(
    client, admin_user, django_user_model, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    png = b"\x89PNG\r\n\x1a\nexample"
    image = ContentFile.objects.create(
        name="Изображение", original_name="image.png", file=upload("image.png", png)
    )
    document = ContentFile.objects.create(
        name="Документ",
        original_name="document.pdf",
        file=upload("document.pdf", b"pdf"),
    )
    url = reverse("admin:content_contentfile_preview", args=[image.pk])
    assert client.get(url).status_code == 302
    staff = django_user_model.objects.create_user(
        email="no-file-access@example.invalid",
        password="example-password",
        is_staff=True,
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content) == png
    assert client.post(url).status_code == 405
    document_url = reverse("admin:content_contentfile_preview", args=[document.pk])
    missing_url = reverse("admin:content_contentfile_preview", args=[999999])
    assert client.get(document_url).status_code == 404
    assert client.get(missing_url).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_file_delete_dialog_uses_django_permissions_and_protection(
    client, admin_user, django_user_model, settings, tmp_path, monkeypatch
):
    from django.contrib import admin

    settings.MEDIA_ROOT = tmp_path
    asset = ContentFile.objects.create(
        name="Тест", original_name="test.txt", file=upload("test.txt", b"example")
    )
    url = reverse("admin:content_contentfile_delete_dialog", args=[asset.pk])
    staff = django_user_model.objects.create_user(
        "file-denied@example.invalid", is_staff=True
    )
    client.force_login(staff)
    assert client.get(url).status_code == 403
    client.force_login(admin_user)
    assert "Удалить файл «Тест»?" in client.get(url).content.decode()
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin_user)
    assert csrf_client.post(url, {"_form_submitted": "on"}).status_code == 403
    model_admin = admin.site._registry[ContentFile]
    original = model_admin.get_deleted_objects
    monkeypatch.setattr(
        model_admin,
        "get_deleted_objects",
        lambda objects, request: (
            ["Файл: Тест", ["Связанная страница"]],
            {"Файлы": 1, "Страницы": 1},
            set(),
            [],
        ),
    )
    assert "Связанная страница" in client.get(url).content.decode()
    monkeypatch.setattr(
        model_admin,
        "get_deleted_objects",
        lambda objects, request: ([], {}, set(), ["Защищённая связь"]),
    )
    blocked = client.get(url)
    assert "data-confirmation-blocked" in blocked.content.decode()
    assert "Защищённая связь" in blocked.content.decode()
    client.post(url, {"_form_submitted": "on"})
    assert ContentFile.objects.filter(pk=asset.pk).exists()
    monkeypatch.setattr(model_admin, "get_deleted_objects", original)
    response = client.post(url, {"_form_submitted": "on"})
    assert response["HX-Redirect"]
    assert not ContentFile.objects.filter(pk=asset.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_admin_upload_is_private_immutable_and_deleted(
    client, admin_user, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    client.force_login(admin_user)
    response = client.post(
        reverse("admin:content_contentfile_add"),
        {"name": "Правила", "file": upload("rules.pdf", b"example")},
    )
    assert response.status_code == 302
    asset = ContentFile.objects.get(name="Правила")
    assert asset.original_name == "rules.pdf"
    assert asset.file.read() == b"example"
    with pytest.raises(ValueError):
        _ = asset.file.url
    path = asset.file.path
    change = reverse("admin:content_contentfile_change", args=[asset.pk])
    response = client.post(
        change,
        {"name": "Правила новые", "file": upload("replacement.pdf", b"replaced")},
    )
    assert response.status_code == 302
    asset.refresh_from_db()
    assert asset.file.read() == b"example"
    response = client.post(
        reverse("admin:content_contentfile_delete", args=[asset.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert not ContentFile.objects.filter(pk=asset.pk).exists()
    assert not Path(path).exists()
