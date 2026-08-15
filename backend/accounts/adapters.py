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
from django.core.exceptions import PermissionDenied
from django.db import transaction

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin

from .models import Invite

INVITE_SESSION_KEY = "invite_token"


def _session_invite(request) -> Invite | None:
    token = request.session.get(INVITE_SESSION_KEY)
    if not token:
        return None
    return Invite.objects.filter(token=token).first()


def _pending_oidc_signup(request) -> bool:
    """True while this account_signup form render/submit is actually
    allauth's *social* signup fallback for an OIDC login, not a real local
    email/password signup.

    allauth's "auto signup" (InviteGatedSocialAccountAdapter.save_user,
    called directly with no form) only fires when it can silently prove the
    social account's email is safe to use -- e.g. it backs off to this form
    instead whenever that email happens to collide with an existing
    (possibly unverified) account, since a hacker could put your address on
    an account they don't own. When that happens, allauth stashes the
    pending SocialLogin in session (see
    socialaccount.internal.flows.signup.redirect_to_signup) and renders
    this same allauth.account.forms.BaseSignupForm -- whose clean_email
    unconditionally calls *this* (account, not social) adapter, with no clue
    it's mid-OIDC-login. Confirmed hitting exactly this: an OIDC first-login
    landed here and got a bogus "invite doesn't cover that email" error,
    even though OIDC is meant to need no invite at all (see module
    docstring). Mirrors InviteGatedSocialAccountAdapter.is_open_for_signup's
    OIDC bypass below, so both entry points agree on when an invite is
    required.
    """
    if not settings.OIDC_AUTO_SIGNUP:
        return False
    data = request.session.get("socialaccount_sociallogin")
    if not data:
        return False
    try:
        sociallogin = SocialLogin.deserialize(data)
    except ValueError:
        return False
    return sociallogin.account.provider == settings.OIDC_PROVIDER_ID


def _claim_invite(request) -> Invite | None:
    """Lock and re-validate the session's invite immediately before an
    account is created from it, inside the caller's transaction.

    is_open_for_signup()/clean_email() only ever check an invite's state at
    a point in time -- without this, two requests holding the same token
    (e.g. two people sent the same link, or one person submitting the
    signup form from two tabs) could both pass those checks before either
    has redeemed it, each creating its own account from what's meant to be
    a single-use token. select_for_update() blocks the second caller until
    the first's transaction commits, so it sees the now-redeemed row and
    raises instead of creating a duplicate account.
    """
    token = request.session.get(INVITE_SESSION_KEY)
    if not token:
        return None
    invite = Invite.objects.select_for_update().filter(token=token).first()
    if invite is None or invite.is_redeemed or invite.is_expired:
        raise PermissionDenied("This invite has already been used.")
    return invite


class NoSelfSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request) -> bool:
        # Email isn't known yet at this point (the signup form hasn't been
        # submitted) -- any email lock on the invite is enforced later, once
        # we do know it (clean_email). This is a cheap up-front check only --
        # see _claim_invite() for the race-safe check actually enforced at
        # account-creation time.
        if _pending_oidc_signup(request):
            return True
        invite = _session_invite(request)
        return invite is not None and not invite.is_redeemed and not invite.is_expired

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        if _pending_oidc_signup(self.request):
            return email
        invite = _session_invite(self.request)
        if invite is None or not invite.is_valid_for_email(email):
            raise forms.ValidationError(
                "This invite link doesn't cover that email address."
            )
        return email

    def save_user(self, request, user, form, commit: bool = True):
        with transaction.atomic():
            invite = _claim_invite(request)
            user = super().save_user(request, user, form, commit=commit)
            if invite is not None:
                invite.redeem(user)
                request.session.pop(INVITE_SESSION_KEY, None)
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
        with transaction.atomic():
            invite = _claim_invite(request)
            user = super().save_user(request, sociallogin, form=form)
            if invite is not None:
                invite.redeem(user)
                request.session.pop(INVITE_SESSION_KEY, None)
        return user
