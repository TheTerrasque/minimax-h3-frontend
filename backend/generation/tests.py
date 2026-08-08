import json
from pathlib import Path

from django.test import SimpleTestCase

from integrations.spectrum import apply_spectrum

# Not settings.RESOURCES_DIR: that only resolves once resources/ is baked
# into the image at Docker build time (see README's "Updating the ComfyUI
# workflows"), same reason scripts/export_workflow_api.py takes an explicit
# path instead of using it. This walks up from backend/generation/ to the
# repo root instead, so the test also works locally / in CI.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ApplySpectrumTests(SimpleTestCase):
    """See extras.md#spectrum / integrations/spectrum.py. Exercises the
    graph splice against the real t2v template rather than a hand-built
    fixture, so this actually breaks if that template's shape ever changes
    (e.g. a re-export moves off a single UNETLoader)."""

    def _load_t2v_workflow(self):
        path = _REPO_ROOT / "resources" / "workflows_api" / "video_minimax_h3_t2v.api.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_splices_in_after_the_sole_unet_loader(self):
        workflow = self._load_t2v_workflow()
        loader_id = next(nid for nid, node in workflow.items() if node["class_type"] == "UNETLoader")

        result = apply_spectrum(workflow)

        spectrum_ids = [nid for nid, node in result.items() if node["class_type"] == "SpectrumApplyMiniMaxH3"]
        self.assertEqual(len(spectrum_ids), 1)
        spectrum_id = spectrum_ids[0]
        self.assertEqual(result[spectrum_id]["inputs"]["model"], [loader_id, 0])

        # Every existing consumer of the loader's output (BasicGuider,
        # BasicScheduler in the real template) now points at Spectrum instead.
        guider = next(node for node in result.values() if node["class_type"] == "BasicGuider")
        scheduler = next(node for node in result.values() if node["class_type"] == "BasicScheduler")
        self.assertEqual(guider["inputs"]["model"], [spectrum_id, 0])
        self.assertEqual(scheduler["inputs"]["model"], [spectrum_id, 0])

    def test_raises_if_not_exactly_one_unet_loader(self):
        with self.assertRaises(RuntimeError):
            apply_spectrum({})
