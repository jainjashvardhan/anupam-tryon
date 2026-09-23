# Try-on proxy backend

Every request goes through Gemini 2.5 Flash Image ("nano banana", ~$0.039/image),
regardless of garment category. Keeps the API key server-side, out of the APK.

## 1. Get a Gemini API key

Get one at https://aistudio.google.com/apikey, put it in `GEMINI_API_KEY`.

## 2. Run locally

```bash
cp .env.example .env   # then fill in GEMINI_API_KEY
pip install -r requirements.txt
uvicorn app:app --reload
```

Test it:

```bash
curl -X POST http://localhost:8000/generate \
  -F "customer_image=@/path/to/customer.jpg" \
  -F "garment_image=@/path/to/shirt.jpg" \
  -F "gender=male" \
  -F "category=shirt" \
  -o result.jpg
```

## 3. Deploy for free (Hugging Face Space, Docker SDK)

1. Create a new Space → SDK: **Docker** → push this `backend/` directory to it (or connect
   it as the Space's git repo).
2. In the Space's **Settings → Repository secrets**, add `GEMINI_API_KEY` instead of
   shipping `.env`.
3. The Space will build the Dockerfile and expose `/generate` at
   `https://your-username-your-space.hf.space/generate`. Point the Flutter app's
   `apiBaseUrl` at that.

Free CPU-basic hosting is enough here - this service does no GPU work itself, it just
forwards image bytes to the Gemini API and returns the result. All cost is per-call to
Gemini (~$0.039/image), nothing else.


## Picture limits

Every picture costs money (Gemini), so `/generate` makes at most **`DAILY_LIMIT` pictures a day
(default 20) and `MONTHLY_LIMIT` a month (default 100)**, counted by Indian date, across every
caller (the shop system and the phone app). Past a limit it answers 429 with a plain sentence
("Today's 20 pictures are used up. More tomorrow."). A picture that fails gives its slot back.
`GET /usage` (shop PIN) returns what is used and left.

The count is `usage/<YYYY-MM>.json` in `USAGE_BUCKET` (defaults to `STORAGE_BUCKET`), changed with
compare-and-swap so parallel instances never overshoot. Without a bucket no picture is made.
Change the limits with `gcloud run services update shop-tryon --update-env-vars DAILY_LIMIT=..`.
Tests: `python -m pytest backend/test_usage.py`.
