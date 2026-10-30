import logging
from uuid import uuid4

from django.conf import settings
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from applications.models import Application, ApplicationAnswer
from festivals.models import Festival
from festivals.services import get_effective_status, synchronize_locked_festival

from .models import User

logger = logging.getLogger(__name__)
VERIFICATION_SALT = "accounts.email-verification"
EMAIL_CHANGE_SALT = "accounts.email-change"
EMAIL_TAKEN = "Этот email уже используется другим аккаунтом."
PENDING_SESSION_KEY = "pending_email_verification_user"


class AccountDeletionConflict(ValidationError):
    pass


def delete_account(user_id, *, actor, password):
    festival_ids = set(
        Application.objects.filter(owner_id=user_id).values_list(
            "festival_id", flat=True
        )
    )
    with transaction.atomic():
        festivals = {
            festival.pk: festival
            for festival in Festival.objects.select_for_update()
            .filter(pk__in=festival_ids)
            .order_by("pk")
        }
        users = {
            user.pk: user
            for user in User.objects.select_for_update()
            .filter(pk__in=[actor.pk, user_id])
            .order_by("pk")
        }
        current_actor, target = users.get(actor.pk), users.get(user_id)
        if current_actor is None or not current_actor.is_active:
            raise PermissionDenied
        if target is None:
            raise AccountDeletionConflict("Аккаунт уже удалён. Обновите страницу.")
        if current_actor.pk != target.pk and not (
            current_actor.is_superuser
            and current_actor.has_perm("accounts.delete_user")
        ):
            raise PermissionDenied
        if not current_actor.check_password(password):
            raise ValidationError({"current_password": "Текущий пароль неверен."})
        now = timezone.now()
        for festival in festivals.values():
            if get_effective_status(festival, at=now) == Festival.Status.ARCHIVED:
                synchronize_locked_festival(festival, at=now)
        applications = list(
            Application.objects.select_for_update().filter(owner=target).order_by("pk")
        )
        if any(
            application.festival_id not in festivals for application in applications
        ):
            raise AccountDeletionConflict(
                "Анкеты изменились. Повторите удаление аккаунта."
            )
        draft_ids = [
            item.pk for item in applications if item.status == Application.Status.DRAFT
        ]
        Application.objects.filter(pk__in=draft_ids).delete()
        history = [
            item for item in applications if item.status != Application.Status.DRAFT
        ]
        ApplicationAnswer.objects.filter(
            application_id__in=[item.pk for item in history]
        ).delete()
        for application in history:
            application.status = Application.Status.WITHDRAWN
            application.owner_id = None
            application.anonymized_at = now
            application.updated_at = now
        Application.objects.bulk_update(
            history, ["owner", "status", "anonymized_at", "updated_at"]
        )
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(User), object_id=str(user_id)
        ).update(object_repr="Удалённый аккаунт", change_message="Аккаунт удалён.")
        if current_actor.pk != target.pk:
            LogEntry.objects.create(
                user=current_actor,
                content_type=ContentType.objects.get_for_model(User),
                object_id=str(user_id),
                object_repr="Удалённый аккаунт",
                action_flag=DELETION,
                change_message="Удаление аккаунта с обезличиванием истории.",
            )
        target.delete()


def verification_token(user):
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=VERIFICATION_SALT)


def verification_user(token, *, lock=False):
    try:
        data = signing.loads(
            token,
            salt=VERIFICATION_SALT,
            max_age=settings.EMAIL_VERIFICATION_TIMEOUT,
        )
        users = User.objects.select_for_update() if lock else User.objects
        user = users.get(
            pk=data["uid"], email=data["email"], email_verified_at__isnull=True
        )
        if user.is_active or user.email_verification_pending:
            return user
    except (signing.BadSignature, User.DoesNotExist, KeyError, TypeError, ValueError):
        return None
    return None


def send_account_email(subject, body, recipient):
    try:
        EmailMessage(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
        ).send()
        return True
    except Exception as error:
        # SMTP exceptions can contain addresses and credentials; don't log their text.
        logger.error("Account email delivery failed (%s).", type(error).__name__)
        return False


def send_verification_email(user, request):
    url = request.build_absolute_uri(
        reverse("accounts:activate", args=[verification_token(user)])
    )
    body = render_to_string("accounts/emails/verification.txt", {"url": url})
    return send_account_email("Подтверждение email — РИ-фест", body, user.email)


def confirm_email(token):
    with transaction.atomic():
        user = verification_user(token, lock=True)
        if user is None:
            return None
        user.is_active = True
        user.email_verification_pending = False
        user.email_verified_at = timezone.now()
        user.save(
            update_fields=[
                "is_active",
                "email_verification_pending",
                "email_verified_at",
            ]
        )
        return user


def change_email(user_id, new_email, password):
    new_email = User.objects.normalize_email(new_email)
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user_id)
            if not user.is_active:
                raise PermissionDenied
            if not user.check_password(password):
                raise ValidationError({"current_password": "Текущий пароль неверен."})
            if new_email == user.email:
                return user, "unchanged"
            if (
                User.objects.filter(email__iexact=new_email)
                .exclude(pk=user.pk)
                .exists()
            ):
                raise ValidationError({"new_email": EMAIL_TAKEN})
            if settings.EMAIL_VERIFICATION_REQUIRED:
                user.pending_email = new_email
                user.email_change_id = uuid4()
                user.save(update_fields=["pending_email", "email_change_id"])
                return user, "pending"
            user.email = new_email
            user.email_verified_at = None
            user.email_verification_pending = True
            user.pending_email = ""
            user.email_change_id = None
            user.save(
                update_fields=[
                    "email",
                    "email_verified_at",
                    "email_verification_pending",
                    "pending_email",
                    "email_change_id",
                ]
            )
            return user, "changed"
    except IntegrityError:
        if User.objects.filter(email__iexact=new_email).exclude(pk=user_id).exists():
            raise ValidationError({"new_email": EMAIL_TAKEN}) from None
        raise


def send_email_change_confirmation(user, request):
    token = signing.dumps(
        {
            "uid": user.pk,
            "email": user.email,
            "new_email": user.pending_email,
            "change_id": str(user.email_change_id),
            "auth": user.get_session_auth_hash(),
        },
        salt=EMAIL_CHANGE_SALT,
    )
    url = request.build_absolute_uri(
        reverse("accounts:email_change_confirm", args=[token])
    )
    body = render_to_string(
        "accounts/emails/email_change.txt",
        {"url": url, "email": user.email, "new_email": user.pending_email},
    )
    return send_account_email(
        "Подтверждение нового email — РИ-фест", body, user.pending_email
    )


def email_change_user(token, *, lock=False):
    try:
        data = signing.loads(
            token, salt=EMAIL_CHANGE_SALT, max_age=settings.EMAIL_VERIFICATION_TIMEOUT
        )
        users = User.objects.select_for_update() if lock else User.objects
        user = users.get(
            pk=data["uid"],
            email=data["email"],
            pending_email=data["new_email"],
            email_change_id=data["change_id"],
            is_active=True,
        )
        if (
            user.pending_email
            and user.email_change_id
            and constant_time_compare(data["auth"], user.get_session_auth_hash())
        ):
            return user
    except (
        signing.BadSignature,
        User.DoesNotExist,
        ValidationError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None
    return None


def confirm_email_change(token):
    try:
        with transaction.atomic():
            user = email_change_user(token, lock=True)
            if user is None:
                return None
            if (
                User.objects.filter(email__iexact=user.pending_email)
                .exclude(pk=user.pk)
                .exists()
            ):
                raise ValidationError(EMAIL_TAKEN)
            user.email = user.pending_email
            user.email_verified_at = timezone.now()
            user.email_verification_pending = False
            user.pending_email = ""
            user.email_change_id = None
            user.save(
                update_fields=[
                    "email",
                    "email_verified_at",
                    "email_verification_pending",
                    "pending_email",
                    "email_change_id",
                ]
            )
            return user
    except IntegrityError:
        # The only unique fields changed here are the email and its Lower constraint.
        raise ValidationError(EMAIL_TAKEN) from None


def cancel_email_change(user_id):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if not user.is_active:
            raise PermissionDenied
        if not user.pending_email:
            return False
        user.pending_email = ""
        user.email_change_id = None
        user.save(update_fields=["pending_email", "email_change_id"])
        return True
