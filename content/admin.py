from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from unfold.admin import ModelAdmin
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldBooleanWidget

from .models import Page, PageVersion


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
