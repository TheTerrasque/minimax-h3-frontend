from django.contrib import admin

from .models import (
    BenchmarkResult,
    GenerationJob,
    PromptChatMessage,
    PromptChatSession,
    ReferenceAsset,
    RenderPreset,
)


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


class PromptChatMessageInline(admin.TabularInline):
    model = PromptChatMessage
    extra = 0
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(PromptChatSession)
class PromptChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "mode", "resulting_job", "created_at", "updated_at")
    list_filter = ("mode",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [PromptChatMessageInline]


@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = (
        "mode",
        "width",
        "height",
        "duration_seconds",
        "steps",
        "status",
        "render_seconds",
        "tested_at",
    )
    list_filter = ("mode", "status")
    readonly_fields = ("tested_at",)
