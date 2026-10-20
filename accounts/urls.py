from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import AccountHomeView, AccountLoginView

app_name = "accounts"

urlpatterns = [
    path("accounts/login/", AccountLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("account/", AccountHomeView.as_view(), name="home"),
]
