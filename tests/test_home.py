import os

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse


@pytest.mark.django_db
def test_home_renders_public_placeholder(client):
    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert {"pages/home.html", "layouts/site.html"} <= {
        template.name for template in response.templates
    }
    assert '<html lang="ru">' in response.text


@pytest.mark.django_db
def test_home_connects_discoverable_stylesheet_and_original_brand(client):
    response = client.get(reverse("home"))

    assert 'href="/static/css/site.css"' in response.text
    assert finders.find("css/site.css") is not None
    assert 'src="/static/brand/ri-fest-2027-avatar.png"' in response.text
    assert finders.find(os.path.normpath("brand/ri-fest-2027-avatar.png")) is not None
