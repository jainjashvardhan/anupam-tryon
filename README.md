# Shop Try-On

Internal virtual try-on tool for shop associates. See `docs/architecture.md` for the
original architecture writeup and current state - note the engine has since been
simplified to Gemini-only for every category (dropped CatVTON) per a later decision.

- `backend/` - FastAPI proxy that sends every request to Gemini 2.5 Flash Image (~$0.039/image).
  Set this up and deploy it first - see `backend/README.md`.
- `mobile/` - Flutter client (image pickers, gender/category selection, result view/save).
  Needs the backend's URL plugged in before building. See `mobile/README.md`.

## Suggested order of operations

1. Get a Gemini API key (`backend/README.md`, step 1).
2. Sanity-check quality manually with a couple of real photos (e.g. via Google AI Studio)
   before writing/running anything else - a shirt, a suit, a saree.
3. Run the backend locally and hit `/generate` with curl to confirm the integration works.
4. Deploy the backend to a free Hugging Face Space.
5. Generate the Flutter platform scaffolding, point it at the deployed backend, build the
   .apk, and sideload it onto one device to test the full flow end-to-end.
6. Pilot with 1-2 associates before rolling out to the rest.
