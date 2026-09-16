# Shop Try-On

AI virtual try-on for a retail clothing shop, built around **Indian ethnic wear** rather
than treating it as an afterthought category. A customer photo + a garment photo goes
in; a photorealistic image of that customer wearing the garment comes out.

See `docs/architecture.md` for the original architecture writeup and current state -
note the engine has since been simplified to Gemini-only for every category (dropped
CatVTON) per a later decision.

## Why it's not just a generic Western-wear VTON tool

Most virtual try-on models - especially open-source VTON diffusion models - are trained
almost entirely on frontal poses and Western-cut garments (t-shirts, jackets, jeans).
Draped and layered Indian garments break that assumption: a saree isn't a top, it's
several metres of fabric pleated at the waist with a pallu over one shoulder; a lehenga
is a separate choli, a long flared skirt and a dupatta; a salwar suit is a kameez over
trousers, not one piece. Getting these right took deliberate, garment-specific prompt
engineering rather than a generic "put this garment on this person" instruction:

- **A construction-aware garment taxonomy** - the backend knows a saree, lehenga,
  anarkali, salwar suit, churidar, sherwani, kurta pyjama, palazzo suit and dhoti are
  each built differently, and tells the model exactly which pieces belong to the outfit
  (e.g. a salwar suit's trousers are part of the garment, not the customer's own) and
  how much of the customer's existing clothing to fully replace vs. leave alone.
- **Pattern and colour fidelity tuned from real usage data** - sarees are the single
  most-tried category in real shop use, and saree borders/pallus routinely mix body
  pattern, border design and figurative motifs (peacocks, elephants, dancing figures)
  that a general-purpose image model tends to flatten into a generic floral border. The
  prompt explicitly calls out figurative motifs, fine repeating textures (leheriya,
  pinstripes, checks) and exact colour/hue preservation as requirements, rather than
  leaving them to the model's default judgement.
- **Occasion-appropriate makeup** - sarees, lehengas, anarkalis and gowns get fuller,
  festive makeup styling (the way they're actually worn for a wedding or function)
  instead of the same light everyday touch-up applied to a Western top, while keeping
  the customer's own face, skin tone and identity locked.
- **Garment-reference isolation** - Indian garment photos are very often product shots
  on a mannequin with a dupatta or pallu propped into shape by hand or a wooden dowel;
  the prompt explicitly excludes any such prop, hand or model from the reference photo
  so it can't bleed into the result as an extra limb.

None of this is exposed as configuration - it lives in the prompt-building logic in
`backend/app.py`, tuned against the categories and failure modes seen in real shop
usage.

## Shape

- `backend/` - FastAPI proxy that sends every request to Gemini 2.5 Flash Image (~$0.039/image).
  Set this up and deploy it first - see `backend/README.md`.
- `mobile/` - Flutter client (image pickers, gender/category selection, result view/save).
  Needs the backend's URL plugged in before building. See `mobile/README.md`.

## Suggested order of operations

1. Get a Gemini API key (`backend/README.md`, step 1).
2. Sanity-check quality manually with a couple of real photos (e.g. via Google AI Studio)
   before writing/running anything else - a shirt, a suit, a saree.
3. Run the backend locally and hit `/generate` with curl to confirm the integration works.
4. Deploy the backend to GCP.
5. Generate the Flutter platform scaffolding, point it at the deployed backend, build the
   .apk, and sideload it onto one device to test the full flow end-to-end.
6. Pilot with 1-2 associates before rolling out to the rest.
