from django.db import models

from .storage import PrivateContentStorage, content_file_path


class ContentFile(models.Model):
    name = models.CharField("Название", max_length=255)
    original_name = models.CharField("Имя файла", max_length=255, editable=False)
    file = models.FileField(
        "Файл", upload_to=content_file_path, storage=PrivateContentStorage()
    )
    created_at = models.DateTimeField("Загружен", auto_now_add=True)

    class Meta:
        verbose_name = "файл"
        verbose_name_plural = "файлы"
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return self.original_name


class Page(models.Model):
    slug = models.SlugField("Адрес", max_length=120, unique=True)
    published_slug = models.SlugField(
        max_length=120, unique=True, null=True, editable=False
    )
    is_home = models.BooleanField("Главная страница", default=False, editable=False)
    revision = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "страница"
        verbose_name_plural = "страницы"
        constraints = [
            models.UniqueConstraint(
                fields=["is_home"],
                condition=models.Q(is_home=True),
                name="content_single_home_page",
            )
        ]

    def __str__(self):
        draft = self.versions.filter(state=PageVersion.State.DRAFT).first()
        return draft.title if draft else self.slug


class PageVersion(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PUBLISHED = "published", "Опубликована"

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="versions")
    state = models.CharField("Состояние", max_length=9, choices=State.choices)
    title = models.CharField("Заголовок", max_length=255)
    login_required = models.BooleanField("Только для авторизованных", default=False)

    class Meta:
        verbose_name = "версия страницы"
        verbose_name_plural = "версии страниц"
        constraints = [
            models.UniqueConstraint(
                fields=["page", "state"], name="content_one_version_per_state"
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["draft", "published"]),
                name="content_valid_page_version_state",
            ),
        ]


class PageBlock(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Текст"
        IMAGE = "image", "Изображение"
        FILE = "file", "Файл"
        FESTIVAL = "festival", "Фестиваль"

    version = models.ForeignKey(
        PageVersion, on_delete=models.CASCADE, related_name="blocks"
    )
    position = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    title = models.CharField(max_length=255, blank=True)
    visible = models.BooleanField(default=True)
    text = models.TextField(blank=True)
    description = models.CharField(max_length=255, blank=True)
    file = models.ForeignKey(
        ContentFile, null=True, blank=True, on_delete=models.PROTECT
    )
    festival = models.ForeignKey(
        "festivals.Festival", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ("position", "pk")

    def __str__(self):
        return (
            f"{self.version.page} — {self.get_kind_display()} "
            f"({self.version.get_state_display()})"
        )
