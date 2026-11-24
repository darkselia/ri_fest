from django.db import models


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
