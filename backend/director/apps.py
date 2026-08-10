from django.apps import AppConfig


class DirectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "director"

    def ready(self):
        # Connects this app's job_finished receiver -- see services.py's
        # on_job_finished(). generation stays ignorant of director (it only
        # ever sends the signal); this is the one place director reaches
        # into generation's lifecycle.
        from . import signals  # noqa: F401
