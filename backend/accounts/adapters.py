"""Invite-only account provisioning -- see features.md ("no random people on
the page, just the ones I invite") and Invite in accounts/models.py.

There is no *open* signup, by either path:

- OIDC login auto-creates an account with no invite needed, because the
  *set of configured OIDC provider apps is itself the gate*: an admin only
  ever wires up an OIDC server (settings.SOCIALACCOUNT_PROVIDERS) they
  already trust to authenticate the right people (e.g. their own identity
  provider), so successfully completing that login already proves the
  person was let in on that server's side.
- Local email/password signup (django-allauth's `account` app) is for
  everyone else -- people who don't have an account on the configured OIDC
  server. It's gated entirely behind the Invite/token flow
  (accounts/views.py): visiting a valid /invite/<token>/ link stashes the
  token in session and sends the visitor to allauth's own signup form
  (account_signup); without a valid token in session, signup is closed.
"""

from django import forms
from django.conf import settings

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .models import Invite

INVITE_SESSION_KEY = "invite_token"


def _session_invite(request) -> Invite | None:
    token = request.session.get(INVITE_SESSION_KEY)
    if not token:
        return None
    return Invite.objects.filter(token=token).first()


class NoSelfSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request) -> bool:
        # Email isn't known yet at this point (the signup form hasn't been
        # submitted) -- any email lock on the invite is enforced later, once
        # we do know it (clean_email).
        invite = _session_invite(request)
        return invite is not None and not invite.is_redeemed and not invite.is_expired

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        invite = _session_invite(self.request)
        if invite is None or not invite.is_valid_for_email(email):
            raise forms.ValidationError(
                "This invite link doesn't cover that email address."
            )
        return email

    def save_user(self, request, user, form, commit: bool = True):
        user = super().save_user(request, user, form, commit=commit)
        token = request.session.pop(INVITE_SESSION_KEY, None)
        if token:
            invite = Invite.objects.filter(token=token).first()
            if invite is not None:
                invite.redeem(user)
        return user


class InviteGatedSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin) -> bool:
        # NOTE: for providers configured via SOCIALACCOUNT_PROVIDERS.APPS
        # (as OIDC is here), allauth sets SocialAccount.provider to that
        # app's `provider_id` (settings.OIDC_PROVIDER_ID, e.g. "oidc") --
        # NOT the provider *type* ("openid_connect"). See
        # SocialAccount.provider's docstring in allauth/socialaccount/models.py.
        if (
            settings.OIDC_AUTO_SIGNUP
            and sociallogin.account.provider == settings.OIDC_PROVIDER_ID
        ):
            return True
        token = request.session.get(INVITE_SESSION_KEY)
        if not token:
            return False
        invite = Invite.objects.filter(token=token).first()
        if invite is None:
            return False
        return invite.is_valid_for_email(sociallogin.user.email or "")

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        token = request.session.pop(INVITE_SESSION_KEY, None)
        if token:
            invite = Invite.objects.filter(token=token).first()
            if invite is not None:
                invite.redeem(user)
        return user
