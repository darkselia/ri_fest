from pathlib import Path
from uuid import uuid4

from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateContentStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError("Файлы страниц не имеют прямого адреса.")


def content_file_path(instance, filename):
    return f"{uuid4().hex}{Path(filename).suffix.lower()}"
