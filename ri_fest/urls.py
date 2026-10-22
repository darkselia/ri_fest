from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.utils.translation import get_language
from django.views.generic import RedirectView, TemplateView
from django.views.i18n import JavaScriptCatalog


class AdminJavaScriptCatalog(JavaScriptCatalog):
    def get_catalog(self):
        catalog = super().get_catalog()
        if (get_language() or "").startswith("ru"):
            # Django 6.1's Russian catalog lacks these new timezone messages.
            for direction, description in (
                ("ahead", "опережает его"),
                ("behind", "отстаёт от него"),
            ):
                key = (
                    "Note: Enter times in the %(timezone)s timezone. "
                    f"(You are %(offset)s hour {direction}.)"
                )
                catalog[key] = [
                    "Примечание: вводите время в часовом поясе %(timezone)s. "
                    f"Ваше время {description} на %(offset)s {unit}."
                    for unit in ("час", "часа", "часов", "часа")
                ]
        return catalog


urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("", include("accounts.urls")),
    path(
        "admin/login/",
        RedirectView.as_view(pattern_name="accounts:login", query_string=True),
    ),
    path("admin/logout/", LogoutView.as_view()),
    path(
        "admin/jsi18n/",
        admin.site.admin_view(
            AdminJavaScriptCatalog.as_view(packages=["django.contrib.admin"])
        ),
    ),
    path("admin/", admin.site.urls),
]
