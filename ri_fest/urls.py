from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect
from django.urls import include, path
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.views.decorators.http import require_POST
from django.views.generic import RedirectView
from django.views.i18n import JavaScriptCatalog

from content.views import page_file, page_view


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


@require_POST
def admin_festival_preference(request):
    request.user.hide_archived_festivals = "hide_archived_festivals" in request.POST
    request.user.save(update_fields=["hide_archived_festivals"])
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = "admin:index"
    return redirect(next_url)


urlpatterns = [
    path("", page_view, name="home"),
    path("info/<slug:slug>/", page_view, name="content_page"),
    path(
        "info/files/<int:page_id>/<int:block_id>/", page_file, name="content_page_file"
    ),
    path("", include("accounts.urls")),
    path("account/applications/", include("applications.urls")),
    path(
        "admin/login/",
        RedirectView.as_view(pattern_name="accounts:login", query_string=True),
    ),
    path("admin/logout/", LogoutView.as_view()),
    path(
        "admin/festival-preference/",
        admin.site.admin_view(admin_festival_preference),
        name="admin_festival_preference",
    ),
    path(
        "admin/jsi18n/",
        admin.site.admin_view(
            AdminJavaScriptCatalog.as_view(packages=["django.contrib.admin"])
        ),
    ),
    path("admin/", admin.site.urls),
]
