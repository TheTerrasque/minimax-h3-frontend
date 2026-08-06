# Image Prompt Writing Guide, Full-Reference Mode (R2I)

## 1. Why This Guide Differs From the Video One

Reference-to-image renders the same reference-to-video pipeline as R2V at
the shortest possible length and keeps only the first frame -- everything
past that frame, and the entire audio track, is discarded. So unlike the
video full-reference guide, this one drops shots, cuts, camera motion,
`overall_soundscape`, `non_diegetic_music`, and dialogue entirely: describe
exactly one moment, informed by the references.

`<Video N>` and reference-audio labels (`<Audio N>`) are rarely useful here
-- they describe motion or sound over time, neither of which survives to a
still image. Only reach for them if the user explicitly wants something
from a reference video/audio's *content* reflected in the still (e.g. a
character's appearance sourced from a reference video frame); otherwise
prefer `<Picture N>`/`<Subject N>`.

## 2. Structure

Two sections, in order:

```text
subject_definitions: ...

description: ...
```

- **`subject_definitions`**: One line per piece of referenced content that
  needs to be tracked -- what the label denotes, and the specific features
  to carry into the image (identity, clothing, colors, materials, style).
  Skip any reference the user attached but the image doesn't actually need.
- **`description`**: A single dense paragraph for the one frame to render:
  style, composition, camera angle, environment, lighting, color and mood
  -- same content as the base (no-reference) image guide's section 2, but
  weaving in the defined subjects/labels at the points where they appear.

## 3. Example

**User's raw idea**: "put the jacket from the second picture on the person
in the first picture, standing in a city street at night"

**Rewritten prompt**:

```text
subject_definitions: <Subject 1> is the person in <Picture 1>, preserving their face, hair, and build. <Subject 2> is the red bomber jacket in <Picture 2>, preserving its color, texture, and zipper/pocket details.

description: Photographic, cinematic still. <Subject 1> stands facing three-quarters toward camera on a rain-slicked city sidewalk at night, now wearing <Subject 2> over dark jeans. Neon signage in magenta and cyan reflects off the wet pavement behind them; a soft rim light from a streetlamp separates their silhouette from the blurred traffic further back. Medium shot, eye-level, shallow depth of field, moody and atmospheric.
```
