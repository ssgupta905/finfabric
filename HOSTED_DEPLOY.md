# Deploy the demo for judges (free)

Three real options. Pick one — all three give judges an HTTPS URL they can open on any device, no install.

| Option | Free tier | Always-on? | Setup time |
|---|---|---|---|
| **Render.com** | ✅ | ❌ sleeps after 15 min idle, ~30 s cold start | ~5 min |
| **HuggingFace Spaces** | ✅ | ✅ always warm | ~10 min |
| **Google Cloud Run** | ✅ (2M req/mo) | ❌ scales to zero, ~5 s cold start | ~15 min |

I recommend **Render** for a hackathon: fastest to set up, works out of the box with `render.yaml`. If you want an always-on URL judges can hit at 3 am, use HuggingFace Spaces.

---

## Prep (do once)

```bash
cd /Users/siddharthasengupta/Downloads/FinFabric
git init -q
git add .
git commit -m "FinFabric demo" -q

# Push to GitHub (needs `brew install gh` or manual repo create)
gh auth login
gh repo create finfabric --public --source=. --remote=origin --push
# or: create a repo manually, then:
#   git remote add origin git@github.com:YOU/finfabric.git
#   git push -u origin main
```

`.env` is git-ignored so your key is not pushed. You'll set it on the host next.

---

## Option 1: Render.com (recommended)

1. Sign up at https://render.com (free, GitHub OAuth).
2. Click **New +** → **Blueprint** → pick your `finfabric` repo.
3. Render reads `render.yaml`, fills everything in. Click **Apply**.
4. When the service is created, open its **Environment** tab and set:
   - `GEMINI_API_KEY` = your key
5. Wait ~2 min for the first build. URL appears at the top, like `https://finfabric-demo.onrender.com`.

Send judges the URL. On the first hit after 15 min of idle they'll see a ~30 s loading spinner while the container wakes — the app works normally after that.

---

## Option 2: HuggingFace Spaces (always-on free)

1. Sign up at https://huggingface.co (free, no card).
2. Click **New Space** → SDK: **Docker**, hardware: **CPU basic (free)**.
3. Clone the Space, copy your project files in, push:
   ```bash
   git clone https://huggingface.co/spaces/YOUR-USERNAME/finfabric
   cd finfabric
   cp -r /Users/siddharthasengupta/Downloads/FinFabric/* .
   git add . && git commit -m "FinFabric" && git push
   ```
4. In the Space **Settings → Variables and secrets**, add:
   - `GEMINI_API_KEY` = your key (as a **Secret**)
5. The Space auto-builds from the `Dockerfile`. URL is `https://YOUR-USERNAME-finfabric.hf.space`.

Advantages over Render: no cold start, no card, permanent free.

---

## Option 3: Google Cloud Run

Needs a credit card on file (free tier is still free — you just have to prove you're not a bot).

```bash
# Install gcloud, log in
gcloud auth login
gcloud config set project YOUR-PROJECT-ID

# Build + push + deploy in one command
gcloud run deploy finfabric-demo \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars USE_LIVE_CHAIN=0,GEMINI_MODEL=gemini-2.5-flash \
  --update-secrets GEMINI_API_KEY=finfabric-gemini-key:latest
```

Cold start is faster than Render (~5 s vs ~30 s). Same free-tier caveat as Render — scales to zero after inactivity.

---

## What judges will see

- URL loads the console
- Bootstrap creates 2 sample epochs on first request (per instance — cold restart resets state; fine for a demo)
- Scenarios, Studio, Credentials drill-down, Adjudicator, Audit, Assistant — all work in fixture mode with real Gemini calls
- No wallet needed, no real chain interaction

## Sanity check post-deploy

```bash
curl https://your-url.onrender.com/api/health
# expect: {"ok": true, "mode": "fixture", "gemini_enabled": true, ...}
```

## Notes

- **Fixture mode is the right choice for judges.** Nothing they do costs you gas or requires them to have a wallet.
- **The Gemini API bill** — 4 features × light usage per judge session ≈ a few thousand tokens each. Free tier of Gemini API covers a lot; watch usage at https://aistudio.google.com/app/apikey.
- **Rate-limiting** — if you're worried about a judge running 100 scenarios in a loop, add `slowapi` to the deps and a decorator on `/api/issue/stream`.
- **In-memory state resets on restart.** Every cold-start creates fresh seeded epochs. That's fine — the demo is stateless from the judge's perspective.
