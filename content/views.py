from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from applications.models import Application
from festivals.models import Festival
from festivals.services import get_effective_status

from .blocks import HTML_CLEANER, IMAGE_EXTENSIONS
from .models import PageBlock, PageVersion


def prepare_blocks(blocks):
    prepared = []
    for block in blocks:
        if not block.visible:
            continue
        if block.kind == "text":
            block.rendered_text = mark_safe(HTML_CLEANER.clean(block.text))
        if block.kind == "festival":
            if not block.festival:
                continue
            status = get_effective_status(block.festival)
            if status == Festival.Status.DRAFT:
                continue
            block.festival_status = (
                "Фестиваль завершён"
                if status == Festival.Status.ARCHIVED
                else Festival.Status(status).label
            )
            block.application_count = (
                0
                if status == Festival.Status.ARCHIVED
                else Application.objects.filter(
                    festival=block.festival, status=Application.Status.ACTIVE
                ).count()
            )
        prepared.append(block)
    return prepared


def page_view(request, slug=None):
    if slug is None:
        version = PageVersion.objects.filter(
            page__is_home=True, state=PageVersion.State.PUBLISHED
        ).first()
        if version is None:
            return render(request, "pages/home.html")
    else:
        version = get_object_or_404(
            PageVersion,
            page__published_slug=slug,
            page__is_home=False,
            state=PageVersion.State.PUBLISHED,
        )
    if version.login_required and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    blocks = prepare_blocks(version.blocks.select_related("file", "festival"))
    return render(request, "content/page.html", {"version": version, "blocks": blocks})


def page_file(request, page_id, block_id):
    block = get_object_or_404(
        PageBlock.objects.select_related("version__page", "file"),
        pk=block_id,
        version__page_id=page_id,
        version__state=PageVersion.State.PUBLISHED,
        visible=True,
        kind__in=["image", "file"],
        file__isnull=False,
    )
    if block.version.login_required and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    try:
        from mimetypes import guess_type
        from pathlib import Path

        is_image = (
            block.kind == "image"
            and Path(block.file.file.name).suffix.lower() in IMAGE_EXTENSIONS
        )
        response = FileResponse(
            block.file.file.open("rb"),
            as_attachment=not is_image,
            filename=block.file.original_name,
            content_type=guess_type(block.file.file.name)[0]
            if is_image
            else "application/octet-stream",
        )
    except FileNotFoundError:
        raise Http404 from None
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, no-store"
    return response
