from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .adapters import INVITE_SESSION_KEY
from .models import Invite


def invite_redeem(request, token: str):
    """Landing page for a shared /invite/<token>/ link.

    Only checks the token itself (exists, unredeemed, unexpired) -- an
    optional email lock on the invite is enforced later, once we actually
    know the visitor's email from the OIDC login
    (InviteGatedSocialAccountAdapter.is_open_for_signup).

    Stashes the token in the session and sends the visitor into OIDC login;
    the invite is only marked redeemed once signup actually succeeds
    (InviteGatedSocialAccountAdapter.save_user).
    """
    invite = Invite.objects.filter(token=token).first()
    if invite is None or invite.is_redeemed or invite.is_expired:
        return HttpResponse("This invite link is invalid or has expired.", status=410)

    request.session[INVITE_SESSION_KEY] = token
    login_url = reverse(
        "openid_connect_login", kwargs={"provider_id": settings.OIDC_PROVIDER_ID}
    )
    return redirect(login_url)
