from django.contrib import admin

from .models import Clip, ClipReferenceAsset, Project, ProjectResource


class ProjectResourceInline(admin.TabularInline):
    model = ProjectResource
    extra = 0
    readonly_fields = ("created_at",)


class ClipReferenceAssetInline(admin.TabularInline):
    model = ClipReferenceAsset
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "created_at", "updated_at")
    search_fields = ("title", "user__username")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProjectResourceInline]


@admin.register(Clip)
class ClipAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "order", "mode", "continues_previous", "needs_render", "current_job")
    list_filter = ("mode", "continues_previous", "needs_render")
    readonly_fields = ("created_at", "updated_at", "checkpoint_filename_prefix", "checkpoint_clip_index")
    inlines = [ClipReferenceAssetInline]
