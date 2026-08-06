# Adds RenderPreset.sort_order (admin-controlled display order, backing
# the new "Quality & Duration" admin tab's reorder tool -- see
# generation/admin_api.py's POST /api/quality-levels/reorder/) and backfills
# the live catalog's 6 known labels to the curated order they already had by
# convention (Draft/Standard/Low/Medium/High/Max, matching their megapixel
# progression), so this migration doesn't visibly change anything until an
# admin actually reorders something. Any other/future label just keeps the
# field's default of 0 -- harmless, it sorts first until explicitly placed.

from django.db import migrations, models

CURATED_ORDER = ["Draft", "Standard", "Low", "Medium", "High", "Max"]


def backfill_sort_order(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    for index, label in enumerate(CURATED_ORDER):
        RenderPreset.objects.filter(label=label).update(sort_order=index)


def noop(apps, schema_editor):
    # Reversing just drops the column (handled by the preceding
    # RemoveField-equivalent migration reversal) -- no data to restore.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0012_standard_tier_and_full_duration_range"),
    ]

    operations = [
        migrations.AddField(
            model_name="renderpreset",
            name="sort_order",
            field=models.IntegerField(
                default=0,
                help_text="Admin-controlled display order (lower first) -- kept in sync "
                "across every mode's row for the same label. See generation/admin_api.py's "
                "reorder endpoint.",
            ),
        ),
        migrations.AlterModelOptions(
            name="renderpreset",
            options={"ordering": ["sort_order", "mode", "megapixels"]},
        ),
        migrations.RunPython(backfill_sort_order, noop),
    ]
