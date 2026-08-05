from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Invite, User

admin.site.register(User, UserAdmin)


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = (
        "token",
        "email",
        "created_by",
        "created_at",
        "expires_at",
        "is_redeemed",
        "redeemed_by",
    )
    readonly_fields = ("token", "created_at", "redeemed_by", "redeemed_at", "invite_path")
    fields = ("email", "expires_at", "created_by", "invite_path", "token", "redeemed_by", "redeemed_at")

    @admin.display(description="Shareable path")
    def invite_path(self, obj: Invite) -> str:
        if not obj.pk:
            return "(save to generate a token)"
        return f"/invite/{obj.token}/"

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
