import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user model (set as AUTH_USER_MODEL from the first migration).

    Kept close to AbstractUser for now; this is the natural place to add
    per-user quotas/preferences later without a disruptive migration.
    """


def _generate_invite_token() -> str:
    return secrets.token_urlsafe(24)


class Invite(models.Model):
    """A one-time link that gates account creation.

    There is no open signup (see accounts/adapters.py): a new account is only
    ever created when a valid, unredeemed, unexpired Invite token was
    presented first. An admin creates these (Django admin) and shares the
    resulting /invite/<token>/ URL out of band.
    """

    token = models.CharField(max_length=64, unique=True, default=_generate_invite_token)
    # Optional: lock the invite to one email address. Left blank, anyone who
    # completes OIDC login while holding the token gets an account.
    email = models.EmailField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    redeemed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_redeemed",
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invite({self.token[:8]}..., redeemed={self.is_redeemed})"

    @property
    def is_redeemed(self) -> bool:
        return self.redeemed_at is not None

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def is_valid_for_email(self, email: str) -> bool:
        if self.is_redeemed or self.is_expired:
            return False
        if self.email and self.email.lower() != email.lower():
            return False
        return True

    def redeem(self, user) -> None:
        self.redeemed_by = user
        self.redeemed_at = timezone.now()
        self.save(update_fields=["redeemed_by", "redeemed_at"])
