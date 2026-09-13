# Shop Try-On

Virtual try-on tool for the associates at Anupam Mall. A customer photo plus a garment photo goes in; an image of that customer wearing that garment comes out.

Read `README.md`, `backend/README.md` and `mobile/README.md` before changing anything.

## Shape

- `backend/` — FastAPI proxy. Every request goes to Gemini 2.5 Flash Image (~$0.039/image). Deployed to a Hugging Face Space.
- `mobile/` — Flutter client: image pickers, gender/category selection, result view and save.

The engine is Gemini-only for every category. CatVTON was dropped in a later decision — do not reintroduce a second engine without asking.

## Hard rules

1. **Never commit `backend/.env`.** It holds a real `GEMINI_API_KEY`. It is gitignored; keep it that way. `backend/.env.example` is the template and stays key-less.
2. Never hardcode an API key, a Space URL with a token, or a bucket name with credentials in tracked files.
3. Customer photos are personal data. Do not log them, do not write them to disk on the backend, do not commit sample photos of real people to the repo. Use synthetic or self-shot test images.
4. Cost per generation is real money. Any change that could cause repeated or retried generation needs an explicit guard and a note in the PR/commit body.
5. Keep the backend a thin proxy. Business logic belongs in the client or nowhere.

## Git and GitHub

**Repo:** `github.com/jainjashvardhan/anupam-tryon` (private). Branch: `main`.

Commit and push without being asked, using conventional commits:
```
feat(backend): add request size guard before Gemini call
fix(mobile): keep result image on orientation change
docs: record the Gemini-only decision
```

Before the first push, run `git status` and read the staged list. Confirm `backend/.env` is not in it.

## Known cleanup

`README.md` points at `/Users/jash.j/.claude/plans/i-want-you-to-imperative-seahorse.md` for the original architecture writeup. That path exists only on the owner's machine and will mean nothing to anyone else, including future sessions. Copy the relevant content into `docs/architecture.md` in this repo and update the link.

## Related project

`../inventory_management` is a separate repo (`anupam-inventory`) — the shop's POS and inventory system. **Do not read or modify it from here.** It will eventually hold thousands of catalogued garment photos, which is exactly the garment-image input this tool needs. That is a future integration to discuss, not something to build unprompted.
