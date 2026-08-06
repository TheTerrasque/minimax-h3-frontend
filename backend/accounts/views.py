from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .adapters import INVITE_SESSION_KEY
from .models import Invite


def invite_redeem(request, token: str):
    """Landing page for a shared /invite/<token>/ link.

    Invites are for people who *don't* already have an account on the
    configured OIDC server (that path auto-creates accounts on login, see
    accounts/adapters.py's module docstring) -- so this sends the visitor to
    allauth's local email/password signup form instead.

    Only checks the token itself (exists, unredeemed, unexpired) -- an
    optional email lock on the invite is enforced later, once we actually
    know the visitor's email from the signup form
    (NoSelfSignupAccountAdapter.clean_email).

    Stashes the token in the session; the invite is only marked redeemed
    once signup actually succeeds (NoSelfSignupAccountAdapter.save_user).
    """
    invite = Invite.objects.filter(token=token).first()
    if invite is None or invite.is_redeemed or invite.is_expired:
        return HttpResponse("This invite link is invalid or has expired.", status=410)

    request.session[INVITE_SESSION_KEY] = token
    return redirect(reverse("account_signup"))
