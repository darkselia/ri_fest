import json
from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, CharField, Exists, OuterRef, Subquery, Value, When
from django.db.models.functions import Coalesce, Concat
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_protect
from unfold.admin import ModelAdmin
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminSelect2Widget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanWidget,
)

from festivals.models import Festival

from .blocks import IMAGE_EXTENSIONS, replace_blocks, serialize_blocks, validate_blocks
from .models import ContentFile, Page, PageBlock, PageVersion
from .uploads import ALLOWED_EXTENSIONS, validate_content_file
from .views import prepare_blocks


class ContentFileForm(forms.ModelForm):
    class Meta:
        model = ContentFile
        fields = ("file",)
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
            self.fields["file"].widget.attrs["accept"] = ",".join(
                sorted(ALLOWED_EXTENSIONS)
            )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        validate_content_file(upload)
        return upload


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
        js = ("js/admin_confirmation_dialog.js",)

    form = ContentFileForm
    list_display = ("preview", "original_name", "created_at", "download")
    list_display_links = ("original_name",)
    search_fields = ("original_name",)
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
            path(
                "upload-inline/",
                self.admin_site.admin_view(self.upload_inline),
                name="content_contentfile_upload_inline",
            ),
            *super().get_urls(),
        ]

    def upload_inline(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_add_permission(request) or not self.has_view_permission(
            request
        ):
            raise PermissionDenied
        form = ContentFileForm(request.POST, request.FILES)
        if not form.is_valid():
            return JsonResponse(
                {
                    "error": " ".join(
                        str(e) for errors in form.errors.values() for e in errors
                    )
                },
                status=400,
            )
        obj = form.save(commit=False)
        obj.original_name = form.cleaned_data["file"].name
        obj.name = obj.original_name
        if (
            request.POST.get("kind") == "image"
            and Path(obj.original_name).suffix.lower() not in IMAGE_EXTENSIONS
        ):
            return JsonResponse(
                {"error": "Выберите изображение PNG, JPEG, WebP или GIF."}, status=400
            )
        obj.save()
        self.log_addition(request, obj, [{"added": {}}])
        return JsonResponse({"id": obj.pk, "name": obj.original_name})

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
        return ("file_name", "created_at") if obj else ("file",)

    @admin.display(description="Файл")
    def file_name(self, obj):
        return obj.original_name

    def save_model(self, request, obj, form, change):
        if not change:
            obj.original_name = form.cleaned_data["file"].name
            obj.name = obj.original_name
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
    blocks = forms.CharField(widget=forms.HiddenInput, required=False)

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
                    blocks=json.dumps(serialize_blocks(draft), ensure_ascii=False),
                )

    def clean_blocks(self):
        value = self.cleaned_data["blocks"]
        if not value:
            draft = (
                self.instance.versions.filter(state=PageVersion.State.DRAFT).first()
                if self.instance.pk
                else None
            )
            return serialize_blocks(draft)
        return validate_blocks(value)

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
    form = PageForm
    change_form_template = "admin/content/page/change_form.html"
    list_display = ("page_title", "address", "publication_status")
    list_display_links = ("page_title",)
    search_fields = ("_list_title", "_list_address")
    actions = None

    def get_queryset(self, request):
        versions = PageVersion.objects.filter(page_id=OuterRef("pk"))
        draft_title = versions.filter(state=PageVersion.State.DRAFT).values("title")[:1]
        queryset = super().get_queryset(request)
        return queryset.annotate(
            _list_title=Coalesce(
                Subquery(draft_title), "slug", output_field=CharField()
            ),
            _list_address=Case(
                When(is_home=True, then=Value("/")),
                default=Concat(
                    Value("/info/"),
                    Coalesce("published_slug", "slug"),
                    Value("/"),
                    output_field=CharField(),
                ),
                output_field=CharField(),
            ),
            _list_published=Exists(versions.filter(state=PageVersion.State.PUBLISHED)),
        )

    @admin.display(description="Страница", ordering="_list_title")
    def page_title(self, obj):
        return obj._list_title

    @property
    def media(self):
        return (
            super().media
            + UnfoldAdminSelect2Widget().media
            + WysiwygWidget().media
            + forms.Media(
                css={"all": ("css/schema_editor.css", "css/content_blocks.css")},
                js=(
                    "js/schema_controls.js",
                    "js/content_page.js",
                    "js/content_blocks.js",
                ),
            )
        )

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)
        if (
            "_save" in request.POST
            and "_popup" not in request.POST
            and self.has_change_permission(request, obj)
        ):
            return redirect("admin:content_page_change", obj.pk)
        return response

    def response_change(self, request, obj):
        response = super().response_change(request, obj)
        if (
            "_save" in request.POST
            and "_popup" not in request.POST
            and self.has_change_permission(request, obj)
        ):
            return redirect("admin:content_page_change", obj.pk)
        return response

    def get_urls(self):
        return [
            path(
                "preview/",
                self.admin_site.admin_view(self.preview_page),
                name="content_page_preview",
            ),
            path(
                "<int:object_id>/copy-blocks/",
                self.admin_site.admin_view(self.copy_blocks),
                name="content_page_copy_blocks",
            ),
            *super().get_urls(),
        ]

    def copy_blocks(self, request, object_id):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        page = get_object_or_404(Page, pk=object_id)
        if not self.has_change_permission(request, page):
            raise PermissionDenied
        draft = get_object_or_404(page.versions, state=PageVersion.State.DRAFT)
        blocks = serialize_blocks(draft)
        if any(block["file_id"] for block in blocks) and not request.user.has_perm(
            "content.view_contentfile"
        ):
            raise PermissionDenied
        return JsonResponse({"blocks": blocks})

    def preview_page(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not (
            self.has_add_permission(request) or self.has_change_permission(request)
        ):
            raise PermissionDenied
        try:
            items = validate_blocks(request.POST.get("blocks", "[]"))
        except ValidationError as error:
            return HttpResponse(" ".join(error.messages), status=400)
        if any(item["file_id"] for item in items) and not request.user.has_perm(
            "content.view_contentfile"
        ):
            raise PermissionDenied
        blocks = [PageBlock(**item) for item in items]
        return render(
            request,
            "content/page.html",
            {
                "version": {"title": request.POST.get("title", "")},
                "blocks": prepare_blocks(blocks),
                "preview": True,
            },
        )

    def get_readonly_fields(self, request, obj=None):
        fields = ("publication_status", "published_address")
        return (*fields, "address") if obj and obj.is_home else fields

    def get_fields(self, request, obj=None):
        address = "address" if obj and obj.is_home else "slug"
        fields = ["title", address, "login_required", "publication_status"]
        if obj and obj.versions.filter(state=PageVersion.State.PUBLISHED).exists():
            fields.append("published_address")
        return (*fields, "version_revision", "blocks")

    @admin.display(description="Адрес", ordering="_list_address")
    def address(self, obj):
        return "/" if obj.is_home else f"/info/{obj.published_slug or obj.slug}/"

    @admin.display(description="Опубликованный адрес")
    def published_address(self, obj):
        return "/" if obj.is_home else f"/info/{obj.published_slug}/"

    @admin.display(description="Статус", ordering="_list_published")
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
                or serialize_blocks(draft) != serialize_blocks(published)
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
                if request.POST.get("blocks") and not request.user.has_perm(
                    "content.view_contentfile"
                ):
                    try:
                        blocks = validate_blocks(request.POST["blocks"])
                    except ValidationError:
                        blocks = []
                    if any(block["file_id"] for block in blocks):
                        raise PermissionDenied
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
                "blocks": json.dumps(serialize_blocks(draft), ensure_ascii=False),
            }
            if draft
            else {},
            "content_files": ContentFile.objects.all()
            if request.user.has_perm("content.view_contentfile")
            else ContentFile.objects.none(),
            "content_festivals": Festival.objects.all(),
            "content_text_widget": WysiwygWidget().render("page_block_text", ""),
            "can_upload_content_file": request.user.has_perm("content.add_contentfile")
            and request.user.has_perm("content.view_contentfile"),
            "content_page_sources": Page.objects.filter(
                versions__state=PageVersion.State.DRAFT
            ).exclude(pk=page.pk if page else None)
            if self.has_change_permission(request)
            else Page.objects.none(),
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
            draft, _ = PageVersion.objects.update_or_create(
                page=page,
                state=PageVersion.State.DRAFT,
                defaults={
                    "title": published.title,
                    "login_required": published.login_required,
                },
            )
            replace_blocks(draft, serialize_blocks(published))
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
        draft, _ = PageVersion.objects.update_or_create(
            page=obj, state=PageVersion.State.DRAFT, defaults=values
        )
        replace_blocks(draft, form.cleaned_data["blocks"])
        if "_publish" in request.POST:
            published, _ = PageVersion.objects.update_or_create(
                page=obj, state=PageVersion.State.PUBLISHED, defaults=values
            )
            replace_blocks(published, form.cleaned_data["blocks"])
