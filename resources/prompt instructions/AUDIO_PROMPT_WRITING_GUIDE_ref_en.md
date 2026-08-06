# Audio Prompt Writing Guide, Full-Reference Mode (R2A)

## 1. Why This Guide Differs From the Video One

Reference-to-audio renders the same reference-to-video pipeline as R2V at
the smallest usable resolution and keeps only the audio track, running for
the full requested clip length. Like the base (no-reference) audio guide,
this one drops shots, cuts, camera motion, and on-screen text entirely --
describe the sound, informed by the references.

`<Audio N>` is the reference type that matters most here -- a reference
clip can hand the model a concrete rhythm, instrument tone, or vocal
timbre to continue or match. `<Picture N>`/`<Subject N>` are still valid
when the user wants a visual reference's *mood or style* reflected
sonically (e.g. a picture of a rainy street implying rain ambience), but
reach for them only when that's actually the intent.

## 2. Structure

Two sections, in order:

```text
subject_definitions: ...

description: ...
```

- **`subject_definitions`**: One line per reference that needs to be
  tracked -- what the label denotes and which specific quality to carry
  forward (a reference audio's instrumentation/rhythm/vocal timbre, or a
  reference image's implied mood/setting). Skip any reference the user
  attached but the audio doesn't actually need.
- **`description`**: The sound itself, written per the base audio guide's
  section 2 (primary sound, rhythm/tempo, dynamics over the clip's
  duration, ambience, mood, `<d>` dialogue tags if there's speech/singing)
  -- weaving in the defined references at the points where they apply.

## 3. Example

**User's raw idea**: "continue the drum pattern from this reference clip
with a bassline added"

**Rewritten prompt**:

```text
subject_definitions: <Subject 1> is the drum pattern in <Audio 1> -- a mid-tempo, syncopated groove with a tight snare and open hi-hats.

description: <Subject 1> continues seamlessly from the reference, now joined by a warm, rounded synth bassline that locks into the kick drum's rhythm. The groove stays steady and confident throughout, with a subtle low-pass filter sweep opening up the bass tone around 00:15.00. Clean, modern production, no other instrumentation. Energetic, driving mood.
```
