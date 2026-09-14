# Architecture

## Current state

- `backend/` — a thin FastAPI proxy. Every request (any garment category, any gender)
  goes to **Gemini 2.5 Flash Image** ("nano banana", ~$0.039/image), authenticating via
  Vertex AI ADC in production (falls back to an AI Studio key for local runs). It builds
  the prompt, calls the model with retry/backoff on quota errors, and returns the image.
  No client-supplied logic beyond category/gender selection - business logic (prompt
  content, makeup styling, pattern-fidelity rules) lives in `backend/app.py`, not spread
  across clients.
- Two clients hit the same `/generate` endpoint:
  - **`backend/static/index.html`** — a single-file web UI served by the backend itself.
    This is the surface actually in day-to-day use by shop associates (opened in a phone
    browser via the deployed Space/Cloud Run URL - no install needed).
  - **`mobile/`** — a Flutter client with the same picker/gender/category/result flow,
    intended for a sideloaded APK. Its platform scaffolding (`android/`, `ios/`) isn't
    generated in this repo; see `mobile/README.md`.
- Generations are archived to a GCS bucket (`STORAGE_BUCKET`, unset = archiving off) -
  customer photo, garment photo, result, and a metadata sidecar per generation,
  partitioned by date. This is what later usage analysis (success rates, category mix,
  failure patterns) is run against. A lifecycle rule deletes objects after 360 days.
  Because this photographs real customers, the UI is explicit with them that the image
  isn't kept **on their device** - it says nothing about server-side retention, which is
  a deliberate choice to keep the archive available for quality review.

## Why Gemini, not a purpose-built VTON model

The original plan (below) considered splitting by engine - a free, self-hosted VTON
model for Western wear and paid Gemini only for sarees, to keep cost near zero. That was
dropped in favor of **Gemini for every category**. Do not reintroduce a second engine
without discussing it first; the reasoning that motivated dropping it (consistency,
avoiding a second infra dependency, saree-grade quality is close enough on other
categories too) still applies unless something has materially changed.

## Original architecture plan (historical)

The section below is preserved from the planning writeup that shaped the initial build.
Its recommendation (split CatVTON/Gemini) is **superseded** - kept here for the
reasoning about the cost/quality/licensing landscape, which is still useful context if a
second engine is ever reconsidered.

### Context

The shop wants an internal Android app (sideloaded .apk, not Play Store) for a handful
of associates: upload a customer photo (any pose) + a garment photo, pick gender +
category (suit, saree, shirt, etc.), and generate an image of the customer wearing that
garment. This is a small shop, used personally by associates for a small set of known
customers - not a large-scale commercial rollout.

One thing worth flagging once, briefly: the popular open VTON models (CatVTON,
IDM-VTON, OOTDiffusion) are licensed CC BY-NC-SA, and that license's non-commercial
restriction is based on *purpose* (for-profit business use), not transaction volume - so
low personal-scale use in a retail shop doesn't formally exempt it. Given the small,
personal, known-customer scope here, this was a judgment call being made knowingly.

### The problem

Two very different technical approaches exist, and the choice drives cost, quality, and
legal risk:

1. **Purpose-built VTON diffusion models** (IDM-VTON, CatVTON, OOTDiffusion, Kolors,
   Leffa) - small, fast, specialized. They warp/inpaint a garment onto a person image.
   Cheap to run, but trained mostly on **frontal, standing poses** and **Western
   upper-body/lower-body/dress categories**. Arbitrary customer poses and non-Western
   garments (sarees especially) are outside their training distribution and will often
   look distorted.
2. **General-purpose multimodal image-editing models** (Gemini 2.5 Flash Image) - not
   VTON-specific, but can follow an instruction like "put this garment on this person"
   and handles arbitrary poses and unusual garments (saree, full suit) far more
   gracefully because it's not doing rigid geometric warping. Costs more per call, but is
   a per-token API cost, not a licensing problem.

**The catch with "free":** the well-known open VTON models are almost all released under
CC BY-NC-SA - non-commercial only (confirmed for IDM-VTON, CatVTON, OOTDiffusion). Using
them, even self-hosted on a cheap GPU, to help sell clothing in a for-profit shop is
technically a license violation, not just a technicality.

One open model, **Kolors Virtual Try-On** (Kuaishou/Kwai), is Apache-2.0 for the code but
requires a free commercial-use registration form with the Kwai team before commercial use
is permitted - worth knowing about, but not pursued given the small personal-use scope.

### Cost/quality landscape (as researched at the time)

| Option | Cost/image | Pose/garment flexibility |
|---|---|---|
| **CatVTON** (self-hosted, free) | $0 (or a few cents off free HF quota) | Good on Western upper/lower-body/dress categories with roughly frontal poses; not trained for saree draping |
| IDM-VTON / OOTDiffusion (self-hosted, free) | $0 | Similar coverage to CatVTON, heavier to run for marginal quality gain |
| Gemini 2.5 Flash Image ("nano banana") API | ~$0.039/image | Handles saree draping and arbitrary poses much better - general instruction-following image edit, not rigid garment warping |

**Original decision (superseded):** use both, split by category - free VTON for Western
wear, paid nano banana only for sarees (and any other case that predictably confuses
VTON models). This kept almost all volume at $0 and reserved the paid, higher-quality
path for the one category where it earned its cost. In practice this was later replaced
by Gemini for every category (see above).

### Key risks noted at the time

- **Customer photo privacy:** photos leave the shop's device to cloud services. The
  original plan assumed not persisting images beyond the session; the as-built system
  archives them to GCS for quality review instead (see "Current state" above) - a
  deliberate later decision, not an oversight.
- **Connectivity:** the design requires internet at the shop for every generation;
  there's no offline mode.
- **Free ZeroGPU reliability:** would have applied to the CatVTON path specifically; not
  applicable now that Gemini handles everything.
- **Saree quality even with nano banana:** flagged as worth checking against real sample
  images before relying on it in front of customers - still an active area of tuning
  (see prompt work in `backend/app.py`).
