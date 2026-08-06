from django.urls import path

from . import api

urlpatterns = [
    path("me/", api.me, name="me"),
    path("invites/", api.invites, name="invites"),
    path("invites/<int:invite_id>/", api.invite_detail, name="invite_detail"),
]
