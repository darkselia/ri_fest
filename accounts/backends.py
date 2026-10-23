from django.conf import settings
from django.contrib.auth.backends import ModelBackend


class EmailVerificationBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and not (
            settings.EMAIL_VERIFICATION_REQUIRED and user.email_verification_pending
        )
