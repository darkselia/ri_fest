from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("", include("accounts.urls")),
    path(
        "admin/login/",
        RedirectView.as_view(pattern_name="accounts:login", query_string=True),
    ),
    path("admin/logout/", LogoutView.as_view()),
    path("admin/", admin.site.urls),
]
