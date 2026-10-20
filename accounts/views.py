from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.urls import reverse
from django.views.generic import TemplateView

from .forms import EmailAuthenticationForm


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin:index")
        return reverse("accounts:home")


class AccountHomeView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/home.html"
