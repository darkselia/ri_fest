from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, render

from .models import PageVersion


def page_view(request, slug=None):
    if slug is None:
        version = PageVersion.objects.filter(
            page__is_home=True, state=PageVersion.State.PUBLISHED
        ).first()
        if version is None:
            return render(request, "pages/home.html")
    else:
        version = get_object_or_404(
            PageVersion,
            page__published_slug=slug,
            page__is_home=False,
            state=PageVersion.State.PUBLISHED,
        )
    if version.login_required and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render(request, "content/page.html", {"version": version})
