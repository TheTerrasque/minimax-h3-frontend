from django.contrib import admin

from .models import GenerationJob, ReferenceAsset, RenderPreset


class ReferenceAssetInline(admin.TabularInline):
    model = ReferenceAsset
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(RenderPreset)
class RenderPresetAdmin(admin.ModelAdmin):
    list_display = (
        "mode",
        "width",
        "height",
        "duration_seconds",
        "steps",
        "estimated_render_seconds",
        "is_draft",
        "is_active",
    )
    list_filter = ("mode", "is_draft", "is_active")


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "mode", "status", "preset", "created_at", "finished_at")
    list_filter = ("mode", "status")
    search_fields = ("user__username", "raw_prompt", "comfyui_prompt_id")
    readonly_fields = ("created_at", "started_at", "finished_at", "q_task_id", "comfyui_prompt_id")
    inlines = [ReferenceAssetInline]
