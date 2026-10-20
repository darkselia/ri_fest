import pytest
from django.contrib.auth.models import Group

PASSWORD = "synthetic-account-password-42"


@pytest.fixture(autouse=True)
def fast_test_passwords(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user("member@example.invalid", PASSWORD)


@pytest.fixture
def organizer(django_user_model):
    user = django_user_model.objects.create_user(
        "organizer@example.invalid", PASSWORD, is_staff=True
    )
    user.groups.add(Group.objects.get(name="Организаторы"))
    return user


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser("admin@example.invalid", PASSWORD)
