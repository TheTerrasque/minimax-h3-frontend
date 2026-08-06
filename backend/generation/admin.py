from django.contrib import admin

from .models import (
    BenchmarkResult,
    GenerationJob,
    PromptChatMessage,
    PromptChatSession,
    ReferenceAsset,
    RenderDuration,
    RenderPreset,
)


class ReferenceAssetInline(admin.TabularInline):
    model = ReferenceAsset
    extra = 0
    readonly_fields = ("created_at",)


class RenderDurationInline(admin.TabularInline):
    model = RenderDuration
    extra = 1


@admin.register(RenderPreset)
class RenderPresetAdmin(admin.ModelAdmin):
    list_display = ("mode", "label", "megapixels", "steps", "is_draft", "is_active", "sort_order")
    list_filter = ("mode", "is_draft", "is_active")
    inlines = [RenderDurationInline]


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "mode",
        "status",
        "preset",
        "width",
        "height",
        "duration_seconds",
        "created_at",
        "finished_at",
    )
    list_filter = ("mode", "status")
    search_fields = ("user__username", "raw_prompt", "comfyui_prompt_id")
    readonly_fields = (
        "created_at",
        "started_at",
        "finished_at",
        "q_task_id",
        "comfyui_prompt_id",
        "megapixels",
        "width",
        "height",
        "duration_seconds",
    )
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
