# Image Prompt Writing Guide (T2I)

## 1. Why This Guide Differs From the Video One

MiniMax H3 has no dedicated image-only model -- image mode renders the same
text-to-video pipeline as T2VA at the shortest possible length and keeps
only the first frame (see the app's own generation pipeline: everything
past that frame, and the entire audio track, is discarded). That means:

- **No shots, no cuts, no camera motion.** Anything written as a second
  shot, a timestamped cut, or a push-in/pan/track will never be reached --
  the render ends before it could happen. Describe exactly one moment.
- **No `overall_soundscape` or `non_diegetic_music`.** The rendered audio
  is thrown away entirely; writing it wastes the model's attention on
  content that will never be heard.
- **No dialogue tags.** Spoken lines never surface in a still frame.

Write a single, dense paragraph describing the one frame to render --
closer to a photography or illustration brief than a screenplay.

## 2. What to Include

- **Style**: `Photographic`, `cinematic still`, `2D-illustrated`, `3D-rendered`,
  `watercolor`, `product photography`, `vintage film`, etc. State it first --
  it sets how everything else should be interpreted.
- **Subject**: who or what is in frame, their appearance, pose, and
  expression, with enough specificity that the result isn't generic.
- **Composition**: framing (close-up, medium shot, wide shot), camera
  angle, and where the subject sits in the frame.
- **Environment**: the setting and any props or background elements that
  matter.
- **Lighting**: direction, quality (soft/hard), color temperature, and any
  practical light sources visible in frame.
- **Color & mood**: a palette or dominant tones, and the overall
  atmosphere.
- **On-screen text**, if any: put the exact text in English double quotes,
  verbatim, unrewritten and untranslated.

Skip any of the above that the user didn't ask for and that isn't needed
to make the image coherent -- don't pad the prompt with invented detail the
user has no stake in.

## 3. Example

**User's raw idea**: "a coffee cup on a desk"

**Rewritten prompt**:

```text
Photographic, product-style still life. A white ceramic coffee cup with a thin gold rim sits slightly off-center on a dark walnut desk, steam curling faintly from the surface. A closed notebook and a fountain pen rest just behind it, softly out of focus. Warm, low-angle window light falls across the scene from the left, casting a long soft shadow to the right; the background fades into a muted, warm-brown blur. Shallow depth of field, eye-level medium close-up, calm and quietly focused mood.
```
