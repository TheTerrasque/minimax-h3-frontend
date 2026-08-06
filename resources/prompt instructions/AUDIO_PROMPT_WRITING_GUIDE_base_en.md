# Audio Prompt Writing Guide (T2A)

## 1. Why This Guide Differs From the Video One

MiniMax H3 has no dedicated audio-only model -- audio mode renders the same
text-to-video pipeline as T2VA at the smallest usable resolution (visual
detail costs nothing to discard, so it's rendered tiny purely for speed)
and keeps only the audio track. Unlike image mode, the render actually
runs for the requested clip length, so duration (and how the sound
develops over that time) matters here -- but the video half never gets
watched. So this guide drops shots, cuts, camera motion, and on-screen
text entirely: **describe the sound, not a scene.**

A plain sound description works well without any of the field labels or
scene-setting the video guide requires -- keep the visual framing minimal
to none unless it genuinely changes what should be heard (e.g. "footsteps
on gravel" needs the surface named, not a shot description of the gravel).

## 2. What to Include

- **Primary sound**: the main instrument(s), voice, or sound source --
  named specifically (`fingerpicked acoustic guitar`, not just `guitar`).
- **Rhythm & tempo**: steady/syncopated, slow/moderate/fast, any notable
  time signature or groove.
- **Dynamics over the clip's duration**: how it changes across the
  requested length -- builds, fades, stays steady, has a clear ending.
  Reference actual time marks (`MM:SS.ss`) for a change of any real
  significance to a duration longer than a few seconds.
- **Ambience/background layer**, if any: room tone, weather, crowd,
  mechanical hum -- whatever sits underneath the primary sound.
- **Mood**: brief and concrete (`tense`, `celebratory`, `melancholic`) --
  avoid vague adjectives that don't translate to an actual sonic choice.

**Speech or singing**: if the user wants spoken/sung content, use the same
tag the video guide uses -- `<d>[Language] exact words</d>` -- with a
speaker ID (`(S1)`, `(S2)`) if there's more than one voice. Preserve the
user's words verbatim inside the tag; describe the voice itself (pitch,
timbre, pacing, accent) just before it.

## 3. Example

**User's raw idea**: "some jazz music, upright bass and brushes"

**Rewritten prompt**:

```text
A relaxed jazz trio: upright bass walking a steady quarter-note line, brushed drums keeping a soft swung rhythm, and a piano comping sparsely between them. Moderate tempo, warm and unhurried. A faint room ambience suggests a small, intimate club -- occasional glass clinks and low murmured conversation well beneath the music. At 00:20.00, the piano briefly steps forward with a short melodic phrase before settling back into the mix. Mellow, late-night mood throughout.
```
