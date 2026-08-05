from django.urls import path

from . import api

urlpatterns = [
    path("health/", api.health, name="health"),
    path("config/", api.config, name="config"),
    path("prompt/refine/", api.refine_prompt, name="refine_prompt"),
    path("prompt/chat/sessions/", api.create_chat_session, name="create_chat_session"),
    path("prompt/chat/sessions/<int:session_id>/", api.get_chat_session, name="get_chat_session"),
    path(
        "prompt/chat/sessions/<int:session_id>/messages/",
        api.post_chat_message,
        name="post_chat_message",
    ),
]
