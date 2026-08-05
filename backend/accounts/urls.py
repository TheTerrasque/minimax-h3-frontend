from django.urls import path

from . import views

urlpatterns = [
    path("invite/<str:token>/", views.invite_redeem, name="invite_redeem"),
]
