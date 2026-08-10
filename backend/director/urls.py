from django.urls import path

from . import api

urlpatterns = [
    path("director/projects/", api.projects, name="director_projects"),
    path("director/projects/<int:project_id>/", api.project_detail, name="director_project_detail"),
    path("director/projects/<int:project_id>/resources/", api.project_resources, name="director_project_resources"),
    path("director/resources/<int:resource_id>/", api.resource_detail, name="director_resource_detail"),
    path("director/projects/<int:project_id>/clips/", api.clips, name="director_clips"),
    path("director/projects/<int:project_id>/render_all/", api.render_all_dirty, name="director_render_all_dirty"),
    path("director/projects/<int:project_id>/plan/", api.plan_project, name="director_plan_project"),
    path("director/projects/<int:project_id>/plan/apply/", api.apply_plan, name="director_apply_plan"),
    path("director/projects/<int:project_id>/assemble/", api.assemble_project, name="director_assemble_project"),
    path("director/clips/<int:clip_id>/", api.clip_detail, name="director_clip_detail"),
    path("director/clips/<int:clip_id>/references/", api.clip_references, name="director_clip_references"),
    path("director/references/<int:reference_id>/", api.clip_reference_detail, name="director_clip_reference_detail"),
    path("director/clips/<int:clip_id>/reorder/", api.reorder_clip, name="director_reorder_clip"),
    path("director/clips/<int:clip_id>/render/", api.render_clip, name="director_render_clip"),
    path("director/clips/<int:clip_id>/cancel/", api.cancel_clip, name="director_cancel_clip"),
]
