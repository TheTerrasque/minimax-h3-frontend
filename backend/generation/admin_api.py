"""Staff-only quality-tier/duration catalog management endpoints.

Backs the in-app admin's "Quality & Duration" tab
(frontend/src/features/admin/CatalogScreen.tsx) -- batch tooling for
RenderPreset/RenderDuration that's otherwise only editable one row at a
time via Django admin. No new models: a "quality level" stays a
convention (RenderPreset rows sharing the same label across modes) rather
than a first-class row -- see RenderPreset's own docstring for why that's
deliberate (megapixels/steps are independently tunable per mode already).

"Removing" something from the matrix is always is_active=False, never a
hard delete: RenderPreset/RenderDuration are both PROTECTed by
GenerationJob, so deleting an in-use row would 500 anyway, and the
is_active fields already exist for exactly this soft-disable purpose.

Same lightweight dict-validation + @extend_schema-for-docs style as
generation/api.py and accounts/api.py's invite endpoints -- see either
file's own docstring for why.
"""

from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import GenerationJob, Mode, RenderDuration, RenderPreset


class CatalogModePresetSerializer(serializers.Serializer):
    preset_id = serializers.IntegerField()
    megapixels = serializers.FloatField()
    steps = serializers.IntegerField()
    is_active = serializers.BooleanField()


class CatalogLevelSerializer(serializers.Serializer):
    label = serializers.CharField()
    is_draft = serializers.BooleanField()
    sort_order = serializers.IntegerField()
    modes = serializers.DictField(
        child=CatalogModePresetSerializer(),
        help_text="Keyed by mode; only present for modes that have a RenderPreset row.",
    )


class CatalogDurationTargetSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    is_active = serializers.BooleanField()
    estimated_render_seconds = serializers.IntegerField(allow_null=True)


class CatalogDurationRowSerializer(serializers.Serializer):
    duration_seconds = serializers.FloatField()
    targets = serializers.DictField(
        child=serializers.DictField(child=CatalogDurationTargetSerializer()),
        help_text="Keyed by level label, then mode.",
    )


class QualityCatalogSerializer(serializers.Serializer):
    modes = serializers.ListField(child=serializers.CharField())
    levels = CatalogLevelSerializer(many=True)
    durations = CatalogDurationRowSerializer(many=True)


def _serialize_level(label: str) -> dict:
    presets = list(RenderPreset.objects.filter(label=label))
    return {
        "label": label,
        "is_draft": presets[0].is_draft if presets else False,
        "sort_order": presets[0].sort_order if presets else 0,
        "modes": {
            p.mode: {
                "preset_id": p.id,
                "megapixels": p.megapixels,
                "steps": p.steps,
                "is_active": p.is_active,
            }
            for p in presets
        },
    }


def _serialize_catalog() -> dict:
    presets = list(RenderPreset.objects.all().order_by("sort_order", "label", "mode"))

    levels: dict[str, dict] = {}
    for p in presets:
        level = levels.setdefault(
            p.label,
            {"label": p.label, "is_draft": p.is_draft, "sort_order": p.sort_order, "modes": {}},
        )
        level["modes"][p.mode] = {
            "preset_id": p.id,
            "megapixels": p.megapixels,
            "steps": p.steps,
            "is_active": p.is_active,
        }

    durations = list(
        RenderDuration.objects.filter(preset__in=presets).select_related("preset")
    )
    duration_index = {
        (d.preset.label, d.preset.mode, d.duration_seconds): d for d in durations
    }
    distinct_seconds = sorted({d.duration_seconds for d in durations})

    duration_rows = []
    for seconds in distinct_seconds:
        targets = {}
        for level in levels.values():
            label_targets = {}
            for mode in level["modes"]:
                d = duration_index.get((level["label"], mode, seconds))
                if d is not None:
                    label_targets[mode] = {
                        "id": d.id,
                        "is_active": d.is_active,
                        "estimated_render_seconds": d.estimated_render_seconds,
                    }
                else:
                    label_targets[mode] = {
                        "id": None,
                        "is_active": False,
                        "estimated_render_seconds": None,
                    }
            targets[level["label"]] = label_targets
        duration_rows.append({"duration_seconds": seconds, "targets": targets})

    return {"modes": Mode.values, "levels": list(levels.values()), "durations": duration_rows}


@extend_schema(
    summary="Full quality/duration catalog (admin)",
    description="Staff only. Read model backing the 'Quality & Duration' admin tab. Unlike "
    "GET /api/presets/, this includes inactive rows (so they can be re-enabled) and every mode.",
    responses={200: QualityCatalogSerializer, 403: OpenApiResponse(description="Not staff.")},
    tags=["admin"],
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def quality_catalog(request):
    return Response(_serialize_catalog())


class CreateModeInputSerializer(serializers.Serializer):
    megapixels = serializers.FloatField()
    steps = serializers.IntegerField()


class CreateQualityLevelRequestSerializer(serializers.Serializer):
    label = serializers.CharField()
    is_draft = serializers.BooleanField(required=False, default=False)
    modes = serializers.DictField(
        child=CreateModeInputSerializer(), help_text="Keyed by mode; at least one required."
    )
    copy_durations_from = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="An existing label to clone active RenderDuration rows from, per included "
        "mode -- omit only if you're about to add durations by hand right after, otherwise the "
        "new level has no selectable lengths and won't be usable on the Generate screen.",
    )


@extend_schema(
    summary="Create a quality level",
    description="Staff only. Creates one RenderPreset per included mode, all sharing `label`.",
    request=CreateQualityLevelRequestSerializer,
    responses={
        201: CatalogLevelSerializer,
        400: OpenApiResponse(description="Invalid input."),
        403: OpenApiResponse(description="Not staff."),
    },
    tags=["admin"],
)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def create_quality_level(request):
    label = str(request.data.get("label") or "").strip()
    if not label:
        return Response({"error": "label is required."}, status=400)
    if RenderPreset.objects.filter(label__iexact=label).exists():
        return Response({"error": f"A quality level named {label!r} already exists."}, status=400)

    is_draft = bool(request.data.get("is_draft", False))
    modes = request.data.get("modes") or {}
    if not isinstance(modes, dict) or not modes:
        return Response({"error": "modes must be a non-empty object keyed by mode."}, status=400)

    parsed: dict[str, tuple[float, int]] = {}
    for mode, cfg in modes.items():
        if mode not in Mode.values:
            return Response({"error": f"Unknown mode {mode!r}."}, status=400)
        if not isinstance(cfg, dict) or "megapixels" not in cfg or "steps" not in cfg:
            return Response({"error": f"modes.{mode} requires megapixels and steps."}, status=400)
        try:
            megapixels, steps = float(cfg["megapixels"]), int(cfg["steps"])
        except (TypeError, ValueError):
            return Response({"error": f"modes.{mode}.megapixels/steps must be numeric."}, status=400)
        if megapixels <= 0 or steps <= 0:
            return Response({"error": f"modes.{mode}.megapixels/steps must be positive."}, status=400)
        parsed[mode] = (megapixels, steps)

    copy_from = str(request.data.get("copy_durations_from") or "").strip()
    next_sort_order = (RenderPreset.objects.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0) + 1

    with transaction.atomic():
        created = {
            mode: RenderPreset.objects.create(
                mode=mode,
                label=label,
                megapixels=mp,
                steps=steps,
                is_draft=is_draft,
                is_active=True,
                sort_order=next_sort_order,
            )
            for mode, (mp, steps) in parsed.items()
        }
        if copy_from:
            source_by_mode = {
                p.mode: p
                for p in RenderPreset.objects.filter(label__iexact=copy_from, mode__in=parsed.keys())
            }
            for mode, new_preset in created.items():
                source = source_by_mode.get(mode)
                if not source:
                    continue
                RenderDuration.objects.bulk_create(
                    [
                        RenderDuration(
                            preset=new_preset,
                            duration_seconds=d.duration_seconds,
                            estimated_render_seconds=d.estimated_render_seconds,
                            is_active=True,
                        )
                        for d in source.durations.filter(is_active=True)
                    ]
                )

    return Response(_serialize_level(label), status=201)


class UpdateModeInputSerializer(serializers.Serializer):
    megapixels = serializers.FloatField(required=False)
    steps = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField(required=False)


class UpdateQualityLevelRequestSerializer(serializers.Serializer):
    new_label = serializers.CharField(required=False)
    is_draft = serializers.BooleanField(required=False)
    sort_order = serializers.IntegerField(
        required=False, help_text="Direct priority-number edit -- see also POST .../reorder/."
    )
    modes = serializers.DictField(child=UpdateModeInputSerializer(), required=False)


@extend_schema(
    summary="Rename/update a quality level",
    description="Staff only. Partial update: new_label renames every RenderPreset row currently "
    "under `label`; is_draft/sort_order apply to all of them too. Each modes.<mode> entry "
    "partially updates that (label, mode) preset if it exists, or creates it (megapixels+steps "
    "required) if it doesn't -- this is how a level picks up a mode it didn't originally have. "
    "A mode key just omitted from the body is left untouched.",
    request=UpdateQualityLevelRequestSerializer,
    responses={
        200: CatalogLevelSerializer,
        400: OpenApiResponse(description="Invalid input."),
        403: OpenApiResponse(description="Not staff."),
        404: OpenApiResponse(description="No such level."),
    },
    tags=["admin"],
)
@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def update_quality_level(request, label: str):
    presets = list(RenderPreset.objects.filter(label=label))
    if not presets:
        return Response({"error": f"No quality level named {label!r}."}, status=404)
    by_mode = {p.mode: p for p in presets}

    new_label = request.data.get("new_label")
    if new_label is not None:
        new_label = str(new_label).strip()
        if not new_label:
            return Response({"error": "new_label can't be blank."}, status=400)
        if new_label.lower() != label.lower() and RenderPreset.objects.filter(
            label__iexact=new_label
        ).exists():
            return Response({"error": f"A quality level named {new_label!r} already exists."}, status=400)

    is_draft = request.data.get("is_draft")

    sort_order = request.data.get("sort_order")
    if sort_order is not None:
        try:
            sort_order = int(sort_order)
        except (TypeError, ValueError):
            return Response({"error": "sort_order must be an integer."}, status=400)

    modes = request.data.get("modes") or {}
    if modes and not isinstance(modes, dict):
        return Response({"error": "modes must be an object keyed by mode."}, status=400)

    mode_updates: dict[str, dict] = {}
    mode_creates: dict[str, tuple[float, int, bool]] = {}
    for mode, cfg in modes.items():
        if mode not in Mode.values:
            return Response({"error": f"Unknown mode {mode!r}."}, status=400)
        if not isinstance(cfg, dict):
            return Response({"error": f"modes.{mode} must be an object."}, status=400)

        if mode not in by_mode:
            if "megapixels" not in cfg or "steps" not in cfg:
                return Response(
                    {"error": f"modes.{mode} requires megapixels and steps to create it."}, status=400
                )
            try:
                megapixels, steps = float(cfg["megapixels"]), int(cfg["steps"])
            except (TypeError, ValueError):
                return Response({"error": f"modes.{mode}.megapixels/steps must be numeric."}, status=400)
            if megapixels <= 0 or steps <= 0:
                return Response({"error": f"modes.{mode}.megapixels/steps must be positive."}, status=400)
            mode_creates[mode] = (megapixels, steps, bool(cfg.get("is_active", True)))
            continue

        fields: dict = {}
        if "megapixels" in cfg:
            try:
                fields["megapixels"] = float(cfg["megapixels"])
            except (TypeError, ValueError):
                return Response({"error": f"modes.{mode}.megapixels must be numeric."}, status=400)
            if fields["megapixels"] <= 0:
                return Response({"error": f"modes.{mode}.megapixels must be positive."}, status=400)
        if "steps" in cfg:
            try:
                fields["steps"] = int(cfg["steps"])
            except (TypeError, ValueError):
                return Response({"error": f"modes.{mode}.steps must be numeric."}, status=400)
            if fields["steps"] <= 0:
                return Response({"error": f"modes.{mode}.steps must be positive."}, status=400)
        if "is_active" in cfg:
            fields["is_active"] = bool(cfg["is_active"])
        mode_updates[mode] = fields

    final_label = new_label if new_label is not None else label

    with transaction.atomic():
        if new_label is not None:
            RenderPreset.objects.filter(label=label).update(label=new_label)
        if is_draft is not None:
            RenderPreset.objects.filter(label=final_label).update(is_draft=bool(is_draft))
        if sort_order is not None:
            RenderPreset.objects.filter(label=final_label).update(sort_order=sort_order)
        for mode, fields in mode_updates.items():
            if fields:
                RenderPreset.objects.filter(id=by_mode[mode].id).update(**fields)
        for mode, (megapixels, steps, is_active) in mode_creates.items():
            RenderPreset.objects.create(
                mode=mode,
                label=final_label,
                megapixels=megapixels,
                steps=steps,
                is_draft=bool(is_draft) if is_draft is not None else presets[0].is_draft,
                sort_order=sort_order if sort_order is not None else presets[0].sort_order,
                is_active=is_active,
            )

    return Response(_serialize_level(final_label), status=200)


class DurationTargetInputSerializer(serializers.Serializer):
    label = serializers.CharField()
    mode = serializers.ChoiceField(choices=Mode.choices)
    is_active = serializers.BooleanField()
    estimated_render_seconds = serializers.IntegerField(required=False)


class UpdateDurationRequestSerializer(serializers.Serializer):
    targets = DurationTargetInputSerializer(many=True)


@extend_schema(
    summary="Set a duration value's availability across quality levels/modes",
    description="Staff only. The batch 'limit duration X to certain quality levels or modes' "
    "tool -- also how a brand new duration value gets introduced (upserts, no separate create "
    "endpoint needed). Each target names a (label, mode) RenderPreset; is_active=true requires "
    "estimated_render_seconds unless a row already exists for that target (then a bare "
    "reactivate reuses its existing value); is_active=false soft-deactivates or no-ops. Every "
    "target is validated before anything is written -- a 400 lists every invalid one, and "
    "nothing partial is applied.",
    request=UpdateDurationRequestSerializer,
    responses={
        200: QualityCatalogSerializer,
        400: OpenApiResponse(description="Invalid input."),
        403: OpenApiResponse(description="Not staff."),
    },
    tags=["admin"],
)
@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def update_duration(request, seconds: str):
    try:
        duration_seconds = float(seconds)
    except ValueError:
        return Response({"error": "seconds must be numeric."}, status=400)

    targets = request.data.get("targets")
    if not isinstance(targets, list) or not targets:
        return Response({"error": "targets must be a non-empty list."}, status=400)

    resolved = []
    errors = []
    for t in targets:
        if not isinstance(t, dict):
            errors.append("each target must be an object.")
            continue
        label, mode, is_active = t.get("label"), t.get("mode"), t.get("is_active")
        if not label or mode not in Mode.values or is_active is None:
            errors.append(f"target {t!r} needs label, a valid mode, and is_active.")
            continue
        preset = RenderPreset.objects.filter(label=label, mode=mode).first()
        if preset is None:
            errors.append(f"No quality level {label!r} for mode {mode!r}.")
            continue

        existing = RenderDuration.objects.filter(preset=preset, duration_seconds=duration_seconds).first()
        estimated = t.get("estimated_render_seconds")
        if is_active and estimated is None and existing is None:
            errors.append(
                f"{label!r}/{mode!r}: estimated_render_seconds is required to activate a new duration."
            )
            continue
        if estimated is not None:
            try:
                estimated = int(estimated)
            except (TypeError, ValueError):
                errors.append(f"{label!r}/{mode!r}: estimated_render_seconds must be numeric.")
                continue
            if estimated <= 0:
                errors.append(f"{label!r}/{mode!r}: estimated_render_seconds must be positive.")
                continue
        resolved.append((preset, existing, bool(is_active), estimated))

    if errors:
        return Response({"error": "; ".join(errors)}, status=400)

    with transaction.atomic():
        for preset, existing, is_active, estimated in resolved:
            if is_active:
                if existing is not None:
                    if estimated is not None:
                        existing.estimated_render_seconds = estimated
                    existing.is_active = True
                    existing.save(update_fields=["estimated_render_seconds", "is_active"])
                else:
                    RenderDuration.objects.create(
                        preset=preset,
                        duration_seconds=duration_seconds,
                        estimated_render_seconds=estimated,
                        is_active=True,
                    )
            elif existing is not None:
                existing.is_active = False
                existing.save(update_fields=["is_active"])

    return Response(_serialize_catalog(), status=200)


class ReorderQualityLevelsRequestSerializer(serializers.Serializer):
    order = serializers.ListField(
        child=serializers.CharField(),
        help_text="Every existing distinct label, in the desired display order.",
    )


@extend_schema(
    summary="Reorder quality levels",
    description="Staff only. `order` must contain exactly the current set of distinct labels "
    "(400 naming any missing/extra ones -- catches stale client state, e.g. someone else "
    "renamed a level mid-drag). Sets sort_order = index for every RenderPreset row under each "
    "label, in one transaction. This also reorders the quality dropdown on the Generate screen, "
    "since GET /api/presets/ relies on the same RenderPreset.Meta.ordering.",
    request=ReorderQualityLevelsRequestSerializer,
    responses={
        200: QualityCatalogSerializer,
        400: OpenApiResponse(description="order doesn't match the current label set."),
        403: OpenApiResponse(description="Not staff."),
    },
    tags=["admin"],
)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def reorder_quality_levels(request):
    order = request.data.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        return Response({"error": "order must be a list of labels."}, status=400)

    current_labels = set(RenderPreset.objects.values_list("label", flat=True).distinct())
    given_labels = set(order)
    if given_labels != current_labels:
        missing = current_labels - given_labels
        extra = given_labels - current_labels
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if extra:
            parts.append(f"unknown {sorted(extra)}")
        return Response({"error": f"order doesn't match the current levels: {'; '.join(parts)}."}, status=400)

    with transaction.atomic():
        for index, label in enumerate(order):
            RenderPreset.objects.filter(label=label).update(sort_order=index)

    return Response(_serialize_catalog(), status=200)


def _ols_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Ordinary least squares for y = intercept + slope*x over raw (x, y)
    points -- deliberately not bucketed/averaged per x, so an x value with
    more completed jobs carries proportionally more weight in the fit."""
    n = len(points)
    x_mean = sum(x for x, _ in points) / n
    y_mean = sum(y for _, y in points) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    return intercept, slope


def _sse(points: list[tuple[float, float]], intercept: float, slope: float) -> float:
    return sum((y - (intercept + slope * x)) ** 2 for x, y in points)


def _find_best_piecewise_fit(points: list[tuple[float, float]]) -> dict | None:
    """Brute-force search over every distinct x value as a candidate single
    breakpoint, fitting two independent OLS lines on either side and taking
    whichever split minimizes total SSE. Returns None if there isn't enough
    data to responsibly attempt it -- each side needs >=3 points AND >=2
    distinct x values among them (a single-x segment has no defined slope)."""
    sorted_points = sorted(points)
    candidates = sorted({x for x, _ in sorted_points})
    best = None
    for split in candidates:
        low = [(x, y) for x, y in sorted_points if x < split]
        high = [(x, y) for x, y in sorted_points if x >= split]
        if len(low) < 3 or len(high) < 3:
            continue
        if len({x for x, _ in low}) < 2 or len({x for x, _ in high}) < 2:
            continue
        low_fit = _ols_fit(low)
        high_fit = _ols_fit(high)
        sse = _sse(low, *low_fit) + _sse(high, *high_fit)
        if best is None or sse < best["sse"]:
            best = {"split": split, "sse": sse, "low": low_fit, "high": high_fit}
    return best


class EstimateDurationsRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    apply = serializers.BooleanField(required=False, default=False)


class EstimateSampleSerializer(serializers.Serializer):
    label = serializers.CharField()
    duration_seconds = serializers.FloatField()
    workload = serializers.FloatField(help_text="steps * megapixels * duration_seconds.")
    render_seconds = serializers.FloatField()


class LinearFitSerializer(serializers.Serializer):
    intercept = serializers.FloatField()
    slope = serializers.FloatField()


class PiecewiseFitSerializer(serializers.Serializer):
    breakpoint_workload = serializers.FloatField()
    segment_low = LinearFitSerializer()
    segment_high = LinearFitSerializer()


class DurationEstimateSerializer(serializers.Serializer):
    label = serializers.CharField()
    duration_seconds = serializers.FloatField()
    current_estimate = serializers.IntegerField(allow_null=True)
    fitted_estimate = serializers.IntegerField()


class EstimateDurationsResponseSerializer(serializers.Serializer):
    fit_available = serializers.BooleanField()
    sample_count = serializers.IntegerField()
    distinct_workloads = serializers.IntegerField()
    model = serializers.ChoiceField(choices=["linear", "piecewise"], required=False)
    linear = LinearFitSerializer(required=False)
    piecewise = PiecewiseFitSerializer(required=False, allow_null=True)
    samples = EstimateSampleSerializer(many=True, required=False)
    estimates = DurationEstimateSerializer(many=True, required=False)


@extend_schema(
    summary="Fit real completed-job render times to estimate durations, pooled across levels",
    description="Staff only. Pulls every real completed job for ANY quality level of this mode "
    "-- status='done' with a real video_file (a 'done' job with no video_file is a failure, not "
    "a success) and both started_at/finished_at set -- and fits against workload = job.steps * "
    "job.megapixels * job.duration_seconds, a proxy for total compute/data. Pooling across "
    "levels (rather than one preset at a time) is deliberate: a completed job on one level and a "
    "completed job on another level at the SAME requested duration land at DIFFERENT workload "
    "values (different megapixels/steps), which is what lets the fit see the gap between levels "
    "and use every level's history at once instead of each preset being starved of its own "
    "sparse data. Needs at least 2 distinct workload values to fit a line; otherwise returns "
    "fit_available=false rather than an error. Also attempts a single-breakpoint piecewise fit "
    "(two independently-fit segments split at whichever workload value minimizes total squared "
    "error) -- only used if it improves on the single line's SSE by >=15% and there are at least "
    "8 total points, guarding against overfitting sparse data; otherwise falls back to the "
    "single line. With apply=true, writes the selected model's fitted estimate onto every "
    "RenderDuration row that already exists for ANY level of this mode (active or not) -- never "
    "creates new rows or changes is_active.",
    request=EstimateDurationsRequestSerializer,
    responses={
        200: EstimateDurationsResponseSerializer,
        400: OpenApiResponse(description="Invalid input."),
        403: OpenApiResponse(description="Not staff."),
    },
    tags=["admin"],
)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def estimate_durations(request):
    mode = request.data.get("mode")
    apply_fit = bool(request.data.get("apply", False))
    if mode not in Mode.values:
        return Response({"error": "a valid mode is required."}, status=400)

    presets = list(RenderPreset.objects.filter(mode=mode))
    completed = (
        GenerationJob.objects.filter(
            mode=mode, status="done", started_at__isnull=False, finished_at__isnull=False
        )
        .exclude(video_file="")
        .select_related("preset")
    )

    samples = []
    points = []
    for job in completed:
        workload = job.steps * job.megapixels * job.duration_seconds
        render_seconds = (job.finished_at - job.started_at).total_seconds()
        samples.append(
            {
                "label": job.preset.label,
                "duration_seconds": job.duration_seconds,
                "workload": workload,
                "render_seconds": render_seconds,
            }
        )
        points.append((workload, render_seconds))

    distinct_workloads = len({x for x, _ in points})
    if distinct_workloads < 2:
        return Response(
            {"fit_available": False, "sample_count": len(points), "distinct_workloads": distinct_workloads}
        )

    intercept, slope = _ols_fit(points)
    linear_sse = _sse(points, intercept, slope)

    model = "linear"
    piecewise = None
    if len(points) >= 8:
        best = _find_best_piecewise_fit(points)
        if best is not None and best["sse"] <= linear_sse * 0.85:
            model = "piecewise"
            piecewise = best

    def fitted_seconds(workload: float) -> int:
        if model == "piecewise":
            fit_intercept, fit_slope = piecewise["low"] if workload < piecewise["split"] else piecewise["high"]
        else:
            fit_intercept, fit_slope = intercept, slope
        return round(max(1, fit_intercept + fit_slope * workload))

    # NOT RenderDuration.objects.values_list("duration_seconds", flat=True)
    # .distinct() -- RenderPreset.Meta.ordering bleeds into the JOIN's
    # implicit ORDER BY, which Postgres then requires in the SELECT list
    # for DISTINCT to be valid, silently turning this into "distinct
    # (duration_seconds, sort_order, mode, megapixels)" instead (342 rows
    # back, not 19). A plain Python set on already-fetched values sidesteps
    # it entirely -- same fix already applied in _serialize_catalog() above.
    all_seconds = sorted({d for d in RenderDuration.objects.values_list("duration_seconds", flat=True)})
    existing = {
        (d.preset_id, d.duration_seconds): d for d in RenderDuration.objects.filter(preset__in=presets)
    }

    estimates = []
    updates = []
    for preset in presets:
        for seconds in all_seconds:
            workload = preset.steps * preset.megapixels * seconds
            fitted = fitted_seconds(workload)
            existing_duration = existing.get((preset.id, seconds))
            estimates.append(
                {
                    "label": preset.label,
                    "duration_seconds": seconds,
                    "current_estimate": existing_duration.estimated_render_seconds
                    if existing_duration
                    else None,
                    "fitted_estimate": fitted,
                }
            )
            if existing_duration is not None:
                updates.append((existing_duration, fitted))

    if apply_fit:
        with transaction.atomic():
            for duration_obj, fitted in updates:
                duration_obj.estimated_render_seconds = fitted
                duration_obj.save(update_fields=["estimated_render_seconds"])

    response = {
        "fit_available": True,
        "sample_count": len(points),
        "distinct_workloads": distinct_workloads,
        "model": model,
        "linear": {"intercept": intercept, "slope": slope},
        "piecewise": None,
        "samples": samples,
        "estimates": estimates,
    }
    if piecewise is not None:
        response["piecewise"] = {
            "breakpoint_workload": piecewise["split"],
            "segment_low": {"intercept": piecewise["low"][0], "slope": piecewise["low"][1]},
            "segment_high": {"intercept": piecewise["high"][0], "slope": piecewise["high"][1]},
        }

    return Response(response)
