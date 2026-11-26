from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_protect
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldBooleanWidget

from .models import ContentFile, Page, PageVersion
from .uploads import ALLOWED_EXTENSIONS, validate_content_file


class ContentFileForm(forms.ModelForm):
    class Meta:
        model = ContentFile
        fields = ("name", "file")
        help_texts = {
            "file": (
                "Не более 20 МБ. Изображения: PNG, JPEG, WebP, GIF; видео: MP4, "
                "WebM, MOV, M4V; аудио: MP3, WAV, OGG, M4A, FLAC; документы: PDF, "
                "TXT, CSV, DOCX, XLSX, PPTX, ODT, ODS, ODP; архивы: ZIP, 7Z, RAR, TAR, GZ."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["name"].required = False
            self.fields["file"].widget.attrs["accept"] = ",".join(
                sorted(ALLOWED_EXTENSIONS)
            )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        validate_content_file(upload)
        return upload

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("name") and cleaned_data.get("file"):
            cleaned_data["name"] = cleaned_data["file"].name
        return cleaned_data


class DeleteFileDialogForm(BaseDialogForm):
    form_before_template = "admin/content/contentfile/delete_dialog_details.html"

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        model_admin = admin.site._registry[ContentFile]
        self.obj = get_object_or_404(ContentFile, pk=object_id)
        if not model_admin.has_delete_permission(request, self.obj):
            raise PermissionDenied
        self.deleted, self.counts, self.perms_lacking, self.protected = (
            model_admin.get_deleted_objects([self.obj], request)
        )

    def clean(self):
        data = super().clean()
        if self.perms_lacking or self.protected:
            raise ValidationError("Этот файл нельзя удалить.")
        return data

    def get_before_template_context(self, request, object_id):
        return {
            "object": self.obj,
            "deleted_objects": self.deleted,
            "model_count": self.counts.items(),
            "has_related_objects": sum(self.counts.values()) > 1,
            "perms_lacking": self.perms_lacking,
            "protected": self.protected,
        }


DELETE_FILE_DIALOG = {
    "title": "Удалить файл",
    "form_submit_text": "Удалить файл",
    "form_class": DeleteFileDialogForm,
}


@admin.register(ContentFile)
class ContentFileAdmin(ModelAdmin):
    class Media:
        js = ("js/content_file.js", "js/admin_confirmation_dialog.js")

    form = ContentFileForm
    list_display = ("preview", "name", "original_name", "created_at", "download")
    list_display_links = ("name",)
    search_fields = ("name", "original_name")
    actions = None

    image_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }

    def get_urls(self):
        return [
            path(
                "<int:object_id>/preview/",
                self.admin_site.admin_view(self.preview_file),
                name="content_contentfile_preview",
            ),
            path(
                "<int:object_id>/download/",
                self.admin_site.admin_view(self.download_file),
                name="content_contentfile_download",
            ),
            path(
                "<int:object_id>/delete-dialog/",
                self.admin_site.admin_view(self.delete_file_dialog),
                name="content_contentfile_delete_dialog",
            ),
            *super().get_urls(),
        ]

    @action(dialog=DELETE_FILE_DIALOG)
    def delete_file_dialog(self, request, form, object_id):
        response = self.delete_view(request, str(object_id))
        if response.status_code in (301, 302, 303):
            return HttpResponse(headers={"HX-Redirect": response.url})
        refreshed = DeleteFileDialogForm(request=request, object_id=object_id)
        return render(
            request,
            "unfold/helpers/dialog.html",
            {
                "dialog": DELETE_FILE_DIALOG,
                "form": refreshed,
                "form_submit_text": DELETE_FILE_DIALOG["form_submit_text"],
            },
        )

    @admin.display(description="Тип")
    def preview(self, obj):
        extension = "." + obj.file.name.rsplit(".", 1)[-1].lower()
        if extension in self.image_types:
            return format_html(
                '<img class="content-file-preview" src="{}" alt="" loading="lazy">',
                reverse("admin:content_contentfile_preview", args=[obj.pk]),
            )
        if extension in {".mp4", ".webm", ".mov", ".m4v"}:
            icon, label = "movie", "Видео"
        elif extension in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
            icon, label = "audio_file", "Аудио"
        elif extension in {".zip", ".7z", ".rar", ".tar", ".gz"}:
            icon, label = "folder_zip", "Архив"
        elif extension in {".xlsx", ".ods", ".csv"}:
            icon, label = "table_chart", "Таблица"
        else:
            icon, label = "description", "Документ"
        return format_html(
            '<span class="content-file-preview material-symbols-outlined" '
            'role="img" aria-label="{}" title="{}">{}</span>',
            label,
            label,
            icon,
        )

    @admin.display(description="Скачать")
    def download(self, obj):
        return mark_safe(
            render_to_string(
                "admin/content/contentfile/download_button.html",
                {
                    "download_url": reverse(
                        "admin:content_contentfile_download", args=[obj.pk]
                    ),
                    "button_attrs": {"aria-label": f"Скачать {obj.original_name}"},
                },
            )
        )

    def download_file(self, request, object_id):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        obj = get_object_or_404(ContentFile, pk=object_id)
        if not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied
        try:
            response = FileResponse(
                obj.file.open("rb"),
                as_attachment=True,
                filename=obj.original_name,
                content_type="application/octet-stream",
            )
        except FileNotFoundError:
            raise Http404 from None
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    def preview_file(self, request, object_id):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        obj = get_object_or_404(ContentFile, pk=object_id)
        if not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied
        extension = "." + obj.file.name.rsplit(".", 1)[-1].lower()
        content_type = self.image_types.get(extension)
        if not content_type:
            raise Http404
        try:
            response = FileResponse(obj.file.open("rb"), content_type=content_type)
        except FileNotFoundError:
            raise Http404 from None
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, no-store"
        return response

    def get_readonly_fields(self, request, obj=None):
        return ("created_at", "file_name") if obj else ()

    def get_fields(self, request, obj=None):
        return ("name", "file_name", "created_at") if obj else ("name", "file")

    @admin.display(description="Файл")
    def file_name(self, obj):
        return obj.original_name

    def save_model(self, request, obj, form, change):
        if not change:
            obj.original_name = form.cleaned_data["file"].name
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        storage, name = obj.file.storage, obj.file.name
        super().delete_model(request, obj)
        transaction.on_commit(lambda: storage.delete(name))


class PageForm(forms.ModelForm):
    title = forms.CharField(
        label="Заголовок", max_length=255, widget=UnfoldAdminTextInputWidget
    )
    login_required = forms.BooleanField(
        label="Только для авторизованных", required=False, widget=UnfoldBooleanWidget
    )
    version_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Page
        fields = ("slug", "title", "login_required")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "slug" in self.fields:
            self.fields["slug"].widget.template_name = "content/widgets/page_slug.html"
            self.fields["slug"].help_text = "При публикации новый адрес заменит старый."
        if self.instance.pk:
            draft = self.instance.versions.filter(state=PageVersion.State.DRAFT).first()
            if draft:
                self.initial.update(
                    title=draft.title,
                    login_required=draft.login_required,
                    version_revision=self.instance.revision,
                )

    def clean_version_revision(self):
        revision = self.cleaned_data["version_revision"]
        if self.instance.pk and revision != self.instance.revision:
            raise ValidationError(
                "Страница уже изменена. Обновите её и повторите изменения."
            )
        return revision

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if (
            Page.objects.filter(published_slug=slug)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError("Этот адрес используется опубликованной страницей.")
        return slug


@admin.register(Page)
class PageAdmin(ModelAdmin):
    class Media:
        js = ("js/content_page.js",)

    form = PageForm
    change_form_template = "admin/content/page/change_form.html"
    list_display = ("__str__", "address", "publication_status")
    actions = None

    def get_readonly_fields(self, request, obj=None):
        fields = ("publication_status", "published_address")
        return (*fields, "address") if obj and obj.is_home else fields

    def get_fields(self, request, obj=None):
        address = "address" if obj and obj.is_home else "slug"
        fields = ["title", address, "login_required", "publication_status"]
        if obj and obj.versions.filter(state=PageVersion.State.PUBLISHED).exists():
            fields.append("published_address")
        return (*fields, "version_revision")

    @admin.display(description="Адрес")
    def address(self, obj):
        return "/" if obj.is_home else f"/info/{obj.published_slug or obj.slug}/"

    @admin.display(description="Опубликованный адрес")
    def published_address(self, obj):
        return "/" if obj.is_home else f"/info/{obj.published_slug}/"

    @admin.display(description="Статус")
    def publication_status(self, obj):
        if not obj or not obj.pk:
            return "Черновик"
        published = obj.versions.filter(state=PageVersion.State.PUBLISHED).first()
        if not published:
            return "Черновик"
        if self.has_unpublished_changes(obj, published):
            return "Опубликована. Есть неопубликованные изменения."
        return "Опубликована"

    @staticmethod
    def has_unpublished_changes(page, published):
        draft = page.versions.filter(state=PageVersion.State.DRAFT).first()
        return bool(
            draft
            and (
                draft.title != published.title
                or draft.login_required != published.login_required
                or (not page.is_home and page.slug != page.published_slug)
            )
        )

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_home) and super().has_delete_permission(request, obj)

    @method_decorator(csrf_protect)
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            with transaction.atomic():
                Page.objects.select_for_update().get(is_home=True)
                page = (
                    get_object_or_404(Page.objects.select_for_update(), pk=object_id)
                    if object_id is not None
                    else None
                )
                if page and (
                    "_discard_changes" in request.POST or "_unpublish" in request.POST
                ):
                    return self.change_publication(request, page)
                return super().changeform_view(
                    request,
                    object_id,
                    form_url,
                    self.page_context(request, page, extra_context),
                )
        page = get_object_or_404(Page, pk=object_id) if object_id else None
        return super().changeform_view(
            request,
            object_id,
            form_url,
            self.page_context(request, page, extra_context),
        )

    def page_context(self, request, page, extra_context):
        published = (
            page.versions.filter(state=PageVersion.State.PUBLISHED).first()
            if page
            else None
        )
        draft = (
            page.versions.filter(state=PageVersion.State.DRAFT).first()
            if published
            else None
        )
        return {
            **(extra_context or {}),
            "page_is_published": bool(published),
            "page_has_changes": bool(
                published and self.has_unpublished_changes(page, published)
            ),
            "can_unpublish_page": bool(
                published and self.has_change_permission(request, page)
            ),
            "page_saved_fields": {
                "title": draft.title,
                "slug": page.slug,
                "login_required": draft.login_required,
            }
            if draft
            else {},
        }

    def change_publication(self, request, page):
        if not self.has_change_permission(request, page):
            raise PermissionDenied
        if request.POST.get("version_revision") != str(page.revision):
            self.message_user(
                request,
                "Страница уже изменена. Обновите её и повторите действие.",
                messages.ERROR,
            )
            return redirect("admin:content_page_change", page.pk)
        published = get_object_or_404(page.versions, state=PageVersion.State.PUBLISHED)
        if "_discard_changes" in request.POST:
            if not page.is_home:
                page.slug = page.published_slug
            PageVersion.objects.update_or_create(
                page=page,
                state=PageVersion.State.DRAFT,
                defaults={
                    "title": published.title,
                    "login_required": published.login_required,
                },
            )
            message = "Неопубликованные изменения отменены."
        else:
            published.delete()
            page.published_slug = None
            message = "Страница снята с публикации."
        page.revision += 1
        page.save(update_fields=["slug", "published_slug", "revision"])
        self.log_change(request, page, message)
        self.message_user(request, message)
        return redirect("admin:content_page_change", page.pk)

    def save_model(self, request, obj, form, change):
        obj.revision += 1
        if "_publish" in request.POST and not obj.is_home:
            obj.published_slug = obj.slug
        obj.save()
        values = {
            "title": form.cleaned_data["title"],
            "login_required": form.cleaned_data["login_required"],
        }
        PageVersion.objects.update_or_create(
            page=obj, state=PageVersion.State.DRAFT, defaults=values
        )
        if "_publish" in request.POST:
            PageVersion.objects.update_or_create(
                page=obj, state=PageVersion.State.PUBLISHED, defaults=values
            )
