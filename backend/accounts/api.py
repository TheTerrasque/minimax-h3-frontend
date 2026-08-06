"""Current-user endpoint for the SPA, plus admin-only invite management.

me() is AllowAny (like generation.api.health/config) so the frontend can
call it on boot to decide between "show the app" and "send the user to
login" without a 403 in the way. The invite endpoints below are the actual
security boundary for who can create/revoke invites -- is_staff on me()'s
response is only there so the frontend can decide whether to show the
"Admin" nav link/route at all; it's UX, not enforcement (see
IsAdminUser on the views themselves).
"""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from .models import Invite


class MeResponseSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    id = serializers.IntegerField(required=False)
    username = serializers.CharField(required=False)
    email = serializers.CharField(required=False)
    is_staff = serializers.BooleanField(
        required=False,
        help_text="Whether this user can manage invites (see IsAdminUser on those views) -- "
        "the frontend uses this only to decide whether to show the Admin nav link/route at all.",
    )


@extend_schema(
    summary="Current user",
    description="AllowAny -- returns {authenticated: false} rather than 403 when logged out, "
    "so the SPA can use this to decide whether to show the login screen.",
    responses=MeResponseSerializer,
    tags=["meta"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False})
    return Response(
        {
            "authenticated": True,
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "is_staff": request.user.is_staff,
        }
    )


class InviteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    token = serializers.CharField(help_text="Combine with the frontend origin to build "
        "/invite/<token>/, the shareable URL.")
    email = serializers.CharField(help_text="Blank if not locked to one address.")
    created_by = serializers.CharField(allow_null=True, help_text="Username, or null.")
    created_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField(allow_null=True)
    is_redeemed = serializers.BooleanField()
    is_expired = serializers.BooleanField()
    redeemed_by = serializers.CharField(allow_null=True, help_text="Username, or null.")
    redeemed_at = serializers.DateTimeField(allow_null=True)


class CreateInviteRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=False, allow_blank=True,
        help_text="Optional -- locks the invite to this address (see Invite.is_valid_for_email). "
        "Blank means anyone holding the link can redeem it.",
    )
    expires_in_days = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="Optional -- computed into expires_at server-side (avoids client/server clock "
        "skew). Omit or null for an invite that never expires.",
    )


def _serialize_invite(invite: Invite) -> dict:
    return {
        "id": invite.id,
        "token": invite.token,
        "email": invite.email,
        "created_by": invite.created_by.username if invite.created_by_id else None,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "is_redeemed": invite.is_redeemed,
        "is_expired": invite.is_expired,
        "redeemed_by": invite.redeemed_by.username if invite.redeemed_by_id else None,
        "redeemed_at": invite.redeemed_at,
    }


@extend_schema(
    methods=["GET"],
    summary="List invites",
    description="Staff only. Newest first, matching Invite.Meta.ordering.",
    responses={200: InviteSerializer(many=True), 403: OpenApiResponse(description="Not staff.")},
    tags=["admin"],
)
@extend_schema(
    methods=["POST"],
    summary="Create an invite",
    description="Staff only. created_by is set to the requesting user automatically.",
    request=CreateInviteRequestSerializer,
    responses={
        201: InviteSerializer,
        400: OpenApiResponse(description="Invalid email/expires_in_days."),
        403: OpenApiResponse(description="Not staff."),
    },
    tags=["admin"],
)
@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def invites(request):
    if request.method == "GET":
        return Response([_serialize_invite(i) for i in Invite.objects.all()])

    email = request.data.get("email", "") or ""
    expires_in_days = request.data.get("expires_in_days")
    expires_at = None
    if expires_in_days not in (None, ""):
        try:
            expires_at = timezone.now() + timedelta(days=int(expires_in_days))
        except (TypeError, ValueError):
            return Response({"error": "expires_in_days must be an integer."}, status=400)

    invite = Invite.objects.create(email=email, expires_at=expires_at, created_by=request.user)
    return Response(_serialize_invite(invite), status=201)


@extend_schema(
    summary="Revoke (delete) an invite",
    description="Staff only. Allowed regardless of redeemed state -- deleting an already-"
    "redeemed invite just removes the audit record, it doesn't affect the account it created.",
    responses={204: OpenApiResponse(description="Deleted."), 403: OpenApiResponse(description="Not staff."),
               404: OpenApiResponse(description="Not found.")},
    tags=["admin"],
)
@api_view(["DELETE"])
@permission_classes([IsAdminUser])
def invite_detail(request, invite_id: int):
    invite = get_object_or_404(Invite, id=invite_id)
    invite.delete()
    return Response(status=204)
