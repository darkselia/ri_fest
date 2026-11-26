from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError

MAX_CONTENT_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".flac",
    ".pdf",
    ".txt",
    ".csv",
    ".docx",
    ".xlsx",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
}
EXECUTABLE_EXTENSIONS = {
    ".apk",
    ".app",
    ".bat",
    ".bin",
    ".cgi",
    ".cmd",
    ".com",
    ".cpl",
    ".deb",
    ".dll",
    ".docm",
    ".dmg",
    ".exe",
    ".hta",
    ".iso",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".msp",
    ".pif",
    ".pl",
    ".php",
    ".pptm",
    ".ps1",
    ".psm1",
    ".py",
    ".pyc",
    ".rb",
    ".reg",
    ".rpm",
    ".scr",
    ".sh",
    ".vbe",
    ".vbs",
    ".wsf",
    ".xll",
    ".xlsm",
    ".xlam",
}


def validate_content_file(upload):
    if upload.size > MAX_CONTENT_FILE_SIZE:
        raise ValidationError("Размер файла не должен превышать 20 МБ.")
    suffixes = [suffix.lower() for suffix in Path(upload.name).suffixes]
    if any(suffix in EXECUTABLE_EXTENSIONS for suffix in suffixes):
        raise ValidationError("Исполняемые файлы и документы с макросами запрещены.")
    suffix = suffixes[-1] if suffixes else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Формат файла не поддерживается. Выберите файл из разрешённого списка."
        )
    try:
        header = upload.read(16)
        if header.startswith(
            (b"MZ", b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")
        ):
            raise ValidationError("Исполняемые файлы загружать нельзя.")
        if suffix == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError("Файл не является изображением PNG.")
        if suffix in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
            raise ValidationError("Файл не является изображением JPEG.")
        if suffix == ".webp" and not (
            header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        ):
            raise ValidationError("Файл не является изображением WebP.")
        if suffix == ".gif" and not header.startswith((b"GIF87a", b"GIF89a")):
            raise ValidationError("Файл не является изображением GIF.")
        if suffix == ".xlsx":
            upload.seek(0)
            try:
                with ZipFile(upload) as book:
                    if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(
                        book.namelist()
                    ):
                        raise ValidationError("Файл не является книгой XLSX.")
            except BadZipFile:
                raise ValidationError("Файл не является книгой XLSX.") from None
    finally:
        upload.seek(0)
