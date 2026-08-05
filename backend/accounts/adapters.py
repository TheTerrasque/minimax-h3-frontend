"""Invite-only account provisioning -- see features.md ("no random people on
the page, just the ones I invite") and Invite in accounts/models.py.

There is no open signup, by either path:

- Local email/password signup (django-allauth's `account` app) is disabled
  outright -- this app only expects OIDC login.
- OIDC login auto-creates an account with no invite needed, because the
  *set of configured OIDC provider apps is itself the gate*: an admin only
  ever wires up an OIDC server (settings.SOCIALACCOUNT_PROVIDERS) they
  already trust to authenticate the right people (e.g. their own identity
  provider), so successfully completing that login already proves the
  person was let in on that server's side.
- The Invite/token flow (accounts/views.py) exists for anything *other* than
  a configured OIDC server -- e.g. if a more open/public social provider is
  ever added later that Isn't itself a closed set of pre-approved people, or
  local accounts are ever re-enabled for a specific person. It is currently
  unused by the OIDC path but kept as the fallback gate for that case.
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .models import Invite

INVITE_SESSION_KEY = "invite_token"

# Provider ids (allauth Provider.id, shared by every app/server configured
# for that provider type -- see SOCIALACCOUNT_PROVIDERS) that are trusted
# outright: an admin only configures an OIDC server they already control who
# has an account on, so no separate invite is required for those logins.
AUTO_ACCEPTED_PROVIDER_IDS = {"openid_connect"}


class NoSelfSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False


class InviteGatedSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin) -> bool:
        if sociallogin.account.provider in AUTO_ACCEPTED_PROVIDER_IDS:
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
