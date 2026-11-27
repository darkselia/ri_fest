import json
from pathlib import Path

from django.core.exceptions import ValidationError

from festivals.models import Festival
from ri_fest.rich_text import HTML_CLEANER

from .models import ContentFile, PageBlock

BLOCK_FIELDS = (
    "kind",
    "title",
    "visible",
    "text",
    "description",
    "file_id",
    "festival_id",
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def serialize_blocks(version):
    return list(version.blocks.values(*BLOCK_FIELDS)) if version else []


def validate_blocks(value):
    block_number = None
    try:
        blocks = json.loads(value) if isinstance(value, str) else value
        if not isinstance(blocks, list) or len(blocks) > 200:
            raise ValueError
        result = []
        for block_number, block in enumerate(blocks, start=1):
            if (
                not isinstance(block, dict)
                or block.get("kind") not in PageBlock.Kind.values
            ):
                raise ValueError
            kind = block["kind"]
            title, text, description = (
                block.get(key, "") for key in ("title", "text", "description")
            )
            if not all(isinstance(item, str) for item in (title, text, description)):
                raise ValueError
            if len(title) > 255 or len(description) > 255 or len(text) > 100000:
                raise ValueError
            visible = block.get("visible", True)
            if not isinstance(visible, bool):
                raise ValueError
            file_id = block.get("file_id") if kind in ("image", "file") else None
            festival_id = block.get("festival_id") if kind == "festival" else None
            if kind in ("image", "file"):
                asset = ContentFile.objects.get(pk=int(file_id))
                if (
                    kind == "image"
                    and Path(asset.file.name).suffix.lower() not in IMAGE_EXTENSIONS
                ):
                    raise ValueError
                file_id = asset.pk
            if festival_id:
                festival_id = Festival.objects.get(pk=int(festival_id)).pk
            result.append(
                dict(
                    kind=kind,
                    title=title,
                    visible=visible,
                    text=HTML_CLEANER.clean(text) if kind == "text" else "",
                    description=description,
                    file_id=file_id,
                    festival_id=festival_id,
                )
            )
        return result
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ContentFile.DoesNotExist,
        Festival.DoesNotExist,
    ):
        raise ValidationError(
            (f"Блок {block_number}. " if block_number is not None else "")
            + "Проверьте тип блока, его содержимое и выбранный файл или фестиваль."
        ) from None


def replace_blocks(version, blocks):
    version.blocks.all().delete()
    PageBlock.objects.bulk_create(
        [
            PageBlock(version=version, position=index, **block)
            for index, block in enumerate(blocks)
        ]
    )
