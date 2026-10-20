import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, connection, transaction

pytestmark = pytest.mark.django_db


def test_user_uses_email_without_username(django_user_model):
    assert django_user_model.USERNAME_FIELD == "email"
    assert django_user_model.REQUIRED_FIELDS == []
    with pytest.raises(FieldDoesNotExist):
        django_user_model._meta.get_field("username")
    assert "accounts_user" in connection.introspection.table_names()
    assert "auth_user" not in connection.introspection.table_names()


def test_create_user_normalizes_email_and_hashes_password(django_user_model):
    user = django_user_model.objects.create_user(
        "  Member@Example.INVALID  ", "synthetic-password"
    )
    assert user.email == "member@example.invalid"
    assert str(user) == user.email
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser
    assert user.groups.count() == 0
    assert user.user_permissions.count() == 0
    assert user.password != "synthetic-password"
    assert user.check_password("synthetic-password")


@pytest.mark.parametrize("email", [None, "", "   "])
def test_create_user_requires_email(django_user_model, email):
    with pytest.raises(ValueError, match="Email must not be empty"):
        django_user_model.objects.create_user(email)


def test_create_user_validates_email(django_user_model):
    with pytest.raises(ValidationError):
        django_user_model.objects.create_user("not-an-email")


def test_user_without_password_cannot_authenticate(django_user_model):
    user = django_user_model.objects.create_user("unusable@example.invalid")
    assert not user.has_usable_password()
    assert authenticate(username=user.email, password="") is None


def test_model_clean_and_save_normalize_email(django_user_model):
    user = django_user_model(email="  Clean@Example.INVALID  ")
    user.clean()
    assert user.email == "clean@example.invalid"
    user.email = "  Saved@Example.INVALID  "
    user.save()
    user.refresh_from_db()
    assert user.email == "saved@example.invalid"


@pytest.mark.parametrize(
    "email", ["member@example.invalid", " MEMBER@EXAMPLE.INVALID "]
)
def test_email_is_unique(django_user_model, member, email):
    with transaction.atomic(), pytest.raises(IntegrityError):
        django_user_model.objects.create_user(email)


def test_database_rejects_case_variant_bypassing_save(django_user_model, member):
    with transaction.atomic(), pytest.raises(IntegrityError):
        django_user_model.objects.bulk_create(
            [django_user_model(email="MEMBER@EXAMPLE.INVALID")]
        )


def test_create_superuser(superuser):
    assert superuser.is_active
    assert superuser.is_staff
    assert superuser.is_superuser
    assert not superuser.groups.exists()


@pytest.mark.parametrize("flag", ["is_staff", "is_superuser"])
def test_superuser_rejects_false_privilege(django_user_model, flag):
    with pytest.raises(ValueError, match=flag):
        django_user_model.objects.create_superuser(
            "admin@example.invalid", **{flag: False}
        )


@pytest.mark.parametrize(
    "email", ["member@example.invalid", " MEMBER@EXAMPLE.INVALID "]
)
def test_authentication_normalizes_email(member, email):
    assert (
        authenticate(username=email, password="synthetic-account-password-42") == member
    )


def test_authentication_rejects_wrong_password_and_inactive_user(member):
    assert authenticate(username=member.email, password="wrong") is None
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert (
        authenticate(username=member.email, password="synthetic-account-password-42")
        is None
    )


def test_organizers_group_is_seeded_without_account_permissions():
    group = Group.objects.get(name="Организаторы")
    assert Group.objects.filter(name="Организаторы").count() == 1
    assert not group.permissions.exists()
