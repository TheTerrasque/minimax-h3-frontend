"""Converts a ComfyUI UI-format workflow (resources/workflows/*.json) into
API-format JSON (what POST /prompt actually accepts), without needing to
open ComfyUI's own UI and click "Export (API)".

Reimplements that export mechanically, using live /object_info schemas from
a running ComfyUI server to know each node class's declared input order and
types. See the module docstring rules below -- each was verified by
inspecting real saved workflow JSON from this project against live
/object_info responses; nothing here is guessed.

Usage:
    uv run python scripts/export_workflow_api.py \\
        "../resources/workflows/video_minimax_h3_t2v.json" \\
        "../resources/workflows_api/video_minimax_h3_t2v.api.json"

COMFYUI_BASE_URL env var overrides the default http://gpusun:8188. Object
info responses are cached alongside this script (object_info_cache/) so
re-running doesn't need the server up every time -- delete that folder to
force a refresh if node schemas change.

Verified serialization rules:

Rule 1: every node's editor `inputs` array lists an entry for every
socket-type input that's currently linked, AND every widget-capable input
that's been "converted to input" (shown via a `widget: {name}` marker) --
regardless of whether it's currently linked. Pure/never-converted widgets
(no entry in `inputs` at all) are purely positional in `widgets_values`.

Rule 2: `widgets_values` always has exactly one entry per widget-capable,
non-autogrow parameter declared on the node class, in object_info's order
(required then optional) -- REGARDLESS of whether that particular parameter
ended up linked (if linked, the entry is a stale leftover; if not, it's the
live value). "Widget-capable" = STRING/INT/FLOAT/BOOLEAN/COMBO and the
dynamic-combo type (COMFY_DYNAMICCOMBO_V3, storing its selected top-level
key as a plain string). Pure reference types (IMAGE/VIDEO/AUDIO/CLIP/VAE/
MODEL/LATENT/CONDITIONING/NOISE/GUIDER/SAMPLER/SIGMAS/MASK) never get a
widgets_values slot -- link-only.

Rule 3: dynamic list/"autogrow" groups (COMFY_AUTOGROW_V3, e.g. MiniMaxH3
ReferenceToVideo's ref_images/ref_videos/ref_video_audios/ref_audios, or
ComfyMathExpression's `values`) do NOT get a widgets_values slot at all.
Each materialized child appears as its own entry in the node's `inputs`
array, named "<group>.<child>" verbatim -- that literal (dotted) name is
used as-is as the API inputs dict key.

Rule 4: subgraph instances flatten by inlining the subgraph definition's
internal nodes directly. An internal link whose origin is the special
inputNode (id -10) resolves by looking up subgraph_def['inputs'][origin_slot]
to get the exposed input's name, then resolving THAT name against the
*subgraph instance* node in the parent graph with the same algorithm (link
if present, else the instance's own widgets_values, positionally counted
the same way as rule 2 but over the subgraph's exposed inputs). A
parent-graph link whose origin is the subgraph instance node itself
redirects to whatever internal node/slot feeds the subgraph's outputNode
(id -20).

As a final safety net, nodes not an ancestor of the SaveVideo node are
dropped -- source workflows can contain leftover/half-wired scratch nodes
(a real example: this project's i2v file has an
ImageScaleToTotalPixels->GetImageSize pair nothing downstream consumes)
that would otherwise ship with a missing required input for no reason.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("COMFYUI_BASE_URL", "http://gpusun:8188")
CACHE_DIR = Path(__file__).parent / "object_info_cache"

PURE_REF_TYPES = {
    "IMAGE", "VIDEO", "AUDIO", "CLIP", "VAE", "MODEL", "LATENT",
    "CONDITIONING", "NOISE", "GUIDER", "SAMPLER", "SIGMAS", "MASK",
}


def load_object_info(class_type):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{class_type}.json"
    if not path.exists():
        with urllib.request.urlopen(f"{BASE_URL}/object_info/{class_type}", timeout=10) as r:
            data = json.load(r)
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    return data[class_type]


def widget_capable_params(class_type):
    """Ordered [(name, is_autogrow)] for widget-capable top-level params
    (required then optional), per rules 2/3."""
    info = load_object_info(class_type)
    out = []
    for group in ("required", "optional"):
        for name, spec in info.get("input", {}).get(group, {}).items():
            typ = spec[0]
            if typ == "COMFY_AUTOGROW_V3":
                out.append((name, True))
            elif isinstance(typ, list):
                out.append((name, False))  # old-style combo: type IS the options list
            elif typ in PURE_REF_TYPES:
                continue
            else:
                out.append((name, False))
    return out


def raw_node_inputs(editor_node):
    """Editor-format node -> {input_name: ("LINK", link_id) | ("VALUE", v)},
    per rules 1-3."""
    class_type = editor_node["type"]
    params = widget_capable_params(class_type)
    non_autogrow_order = [n for n, is_ag in params if not is_ag]

    editor_inputs_by_name = {i["name"]: i for i in editor_node.get("inputs", [])}
    widgets_values = editor_node.get("widgets_values") or []

    result = {}
    for idx, name in enumerate(non_autogrow_order):
        entry = editor_inputs_by_name.get(name)
        if entry is not None and entry.get("link") is not None:
            result[name] = ("LINK", entry["link"])
        elif idx < len(widgets_values):
            result[name] = ("VALUE", widgets_values[idx])

    handled = set(non_autogrow_order)
    for name, entry in editor_inputs_by_name.items():
        if name in handled:
            continue
        if entry.get("link") is not None:
            result[name] = ("LINK", entry["link"])
        # unlinked optional pure-ref/autogrow-child inputs: omitted entirely

    return result


def _links_by_id(raw_links):
    """Normalizes to {id: (origin_id, origin_slot, target_id)}.

    Top-level graph links are legacy flat arrays
    [id, origin_id, origin_slot, target_id, target_slot, type]; subgraph
    internal links are dicts. Both forms show up in these files.
    """
    out = {}
    for l in raw_links:
        if isinstance(l, dict):
            out[l["id"]] = (l["origin_id"], l["origin_slot"], l["target_id"])
        else:
            link_id, origin_id, origin_slot, target_id = l[0], l[1], l[2], l[3]
            out[link_id] = (origin_id, origin_slot, target_id)
    return out


def build(ui_workflow):
    nodes = ui_workflow.get("nodes", [])
    top_links_by_id = _links_by_id(ui_workflow.get("links", []))
    subgraphs = ui_workflow.get("definitions", {}).get("subgraphs", [])
    subgraph_by_type_id = {sg["id"]: sg for sg in subgraphs}

    subgraph_output_redirect = {}
    subgraph_instances = {}
    for node in nodes:
        sg_def = subgraph_by_type_id.get(node["type"])
        if sg_def is None:
            continue
        sg_links_by_id = _links_by_id(sg_def["links"])
        subgraph_instances[node["id"]] = (node, sg_def, sg_links_by_id)
        for origin_id, origin_slot, target_id in sg_links_by_id.values():
            if target_id == -20:
                subgraph_output_redirect[node["id"]] = (origin_id, origin_slot)

    def resolve(origin_id, origin_slot, links_by_id, boundary_ctx):
        if origin_id == -10:
            instance_node, sg_def, outer_links_by_id, outer_boundary_ctx = boundary_ctx
            exposed = sg_def["inputs"][origin_slot]
            exposed_name = exposed["name"]
            instance_inputs_by_name = {i["name"]: i for i in instance_node.get("inputs", [])}
            entry = instance_inputs_by_name.get(exposed_name)
            if entry is not None and entry.get("link") is not None:
                o_id, o_slot, _ = outer_links_by_id[entry["link"]]
                return resolve(o_id, o_slot, outer_links_by_id, outer_boundary_ctx)
            if exposed["type"] in PURE_REF_TYPES:
                return ("OMIT",)
            widget_exposed = [
                i["name"] for i in sg_def["inputs"] if i["type"] not in PURE_REF_TYPES
            ]
            idx = widget_exposed.index(exposed_name)
            return ("VALUE", instance_node["widgets_values"][idx])

        if origin_id in subgraph_output_redirect:
            real_id, real_slot = subgraph_output_redirect[origin_id]
            inst_node, sg_def, sg_links_by_id = subgraph_instances[origin_id]
            return resolve(
                real_id, real_slot, sg_links_by_id, (inst_node, sg_def, links_by_id, boundary_ctx)
            )

        return ("NODE", str(origin_id), origin_slot)

    api = {}

    def emit(node_id, class_type, raw_inputs, links_by_id, boundary_ctx):
        resolved = {}
        for name, (kind, payload) in raw_inputs.items():
            if kind == "VALUE":
                resolved[name] = payload
                continue
            origin_id, origin_slot, _ = links_by_id[payload]
            outcome = resolve(origin_id, origin_slot, links_by_id, boundary_ctx)
            if outcome[0] == "OMIT":
                continue
            elif outcome[0] == "VALUE":
                resolved[name] = outcome[1]
            else:
                _, node_id_str, slot = outcome
                resolved[name] = [node_id_str, slot]
        api[str(node_id)] = {
            "class_type": class_type,
            "inputs": resolved,
            "_meta": {"title": class_type},
        }

    for node in nodes:
        node_id = node["id"]
        node_type = node["type"]

        if node_id in subgraph_instances:
            _, sg_def, sg_links_by_id = subgraph_instances[node_id]
            boundary_ctx = (node, sg_def, top_links_by_id, None)
            for inner_node in sg_def["nodes"]:
                raw = raw_node_inputs(inner_node)
                emit(inner_node["id"], inner_node["type"], raw, sg_links_by_id, boundary_ctx)
            continue

        if node_type == "MarkdownNote":
            continue

        raw = raw_node_inputs(node)
        emit(node_id, node_type, raw, top_links_by_id, None)

    return _prune_unreachable(api)


def _prune_unreachable(api):
    """Drops nodes not an ancestor of the SaveVideo node (see module docstring)."""
    roots = [nid for nid, n in api.items() if n["class_type"] == "SaveVideo"]
    keep = set()
    stack = list(roots)
    while stack:
        nid = stack.pop()
        if nid in keep:
            continue
        keep.add(nid)
        for val in api[nid]["inputs"].values():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                stack.append(val[0])
    dropped = sorted(set(api) - keep, key=int)
    if dropped:
        print("  pruned unreachable nodes:", dropped, file=sys.stderr)
    return {nid: n for nid, n in api.items() if nid in keep}


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <ui-format-workflow.json> <output-api-format.json>")
        raise SystemExit(1)
    in_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    ui_workflow = json.loads(in_path.read_text(encoding="utf-8"))
    result = build(ui_workflow)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{in_path.name} -> {len(result)} nodes -> {out_path}")


if __name__ == "__main__":
    main()
