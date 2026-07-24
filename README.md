# Prompt Relay

A **broken-telephone** prompting party game. A group of 3–4 passes a **target
image** — defined by exactly **10 details** — down a chain of three 45-second
prompts. **Only Player 1 sees the target**; they describe it and the AI draws a
picture. Each later player sees **only the image the player before them made**,
describes what they see, and the AI **redraws it from scratch** — so the picture
drifts from the original like real broken telephone. At the end an AI vision judge
scores the final image out of 10, similarity breaks ties, and results land on a
**live leaderboard**.

> Built with FastAPI + Jinja2 (no build step), Supabase (Postgres + Storage +
> realtime), and pluggable Gemini/OpenAI image + judge providers with automatic
> fallback. A keyless **mock provider** lets the whole game run without any AI
> keys for testing and demos.

## How a game flows

1. **Group name + size** entered → a game is created and a target assigned, then
   play jumps straight into Step 1.
2. **Step 1** (Player 1): sees the **target** and has 45s to describe it → AI
   **draws** the base image.
3. **Step 2** (Player 2): sees **only Player 1's image** (not the target), describes
   it in 45s → AI **redraws** a fresh image from that description.
4. **Step 3** (Player 3, or Players 3 & 4 paired in a 4-person group): sees **only
   Player 2's image**, describes it → AI redraws the final image.
5. **Reveal**: the target, the full relay, and a single **percentage** score — the
   first time anyone but Player 1 sees the original. The individual target details
   (and the fact there are exactly 10) are hidden here so a finished group can't
   leak the target to groups still to play.
6. Result is appended to the **leaderboard** (ranked by detail score, then
   similarity). The board shows one percentage per group; clicking a group opens a
   modal with its per-detail breakdown and a button to download its final image.

**No talking or coaching** between players — the image is the only thing passed down
the chain (that's what makes it broken telephone).

Empty box at timeout = a forfeited turn (image kept unchanged). If Step 1 is
forfeited, the base stays blank and the next non-empty prompt generates it.

## Requirements

- Python 3.11+
- A Supabase project (Postgres + Storage + realtime)
- An **OpenAI API key** (recommended — it's the working image/judge provider). A
  Gemini key is optional and, for image generation, requires a billing-enabled
  Google project (see [AI providers](#ai-providers)). With no keys at all, the
  keyless mock provider runs the whole game.

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the Supabase backend

1. Create a project at [supabase.com](https://supabase.com).
2. In the **SQL editor**, run [`supabase/schema.sql`](supabase/schema.sql). This
   creates the `games` and `leaderboard` tables, RLS policies (anon may only
   *read* the leaderboard), the realtime publication, and the public `images`
   Storage bucket. The file is idempotent and includes `alter table … add column
   if not exists group_id` statements, so **re-running it on an existing project**
   adds the participant-entered Group ID column without touching your data.
3. Confirm a public bucket named **`images`** exists under **Storage** (the SQL
   creates it; you can also create it from the UI).

### 3. Configure environment

Copy `.env.example` to `.env` and fill in:

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (server-side only), `SUPABASE_ANON_KEY`
  (Project Settings → API).
- `OPENAI_API_KEY` for real images/judging (recommended). `GEMINI_API_KEY` is
  optional. Leave both blank to run on the keyless mock (`ENABLE_MOCK=true`).
- `PROVIDER_ORDER` chooses which real providers are tried, in order. This project
  ships as `PROVIDER_ORDER=openai` because Gemini image generation needs a paid
  Google account (see [AI providers](#ai-providers)); switch to `gemini,openai`
  once Gemini billing is enabled.
- `SITE_PASSWORD` gates the whole site behind a static password. Leave it **blank**
  to run open (local dev/tests). When set, every page except `/login` and static
  assets requires it, enforced by an ASGI middleware and a signed cookie — so it
  works the same locally and on Vercel. On Vercel, set `SITE_PASSWORD` (and
  optionally `SITE_SECRET`) in **Project → Settings → Environment Variables**. The
  live leaderboard's realtime updates still work on any device once past the gate
  (the browser subscribes to Supabase directly with the read-only anon key).

### 4. Seed the reference pool

A pool of fifteen real illustrations (generated with `gpt-image-1`) is defined
under `references/`, each containing all 10 of its target details. To generate
any missing targets with the configured image AI and upload them to Storage:

```bash
python scripts/prepare_reference.py --seed          # uses your AI keys, or the mock
python scripts/prepare_reference.py --seed --no-upload   # local only, no Storage
python scripts/prepare_reference.py --seed --force  # regenerate ALL, overwriting existing

# Regenerate every target as high-quality art (recommended for the reference pool):
python scripts/prepare_reference.py --seed --force --quality high --no-upload
```

`--seed` is idempotent: any target that already has an `image.png` + `details.json`
is skipped, so re-running it only fills in newly added targets (use `--force` to
regenerate everything).

`--quality {low,medium,high,auto}` sets the image tier for **that run only**,
overriding `OPENAI_IMAGE_QUALITY`. This matters because that env var is shared with
live gameplay, where it is deliberately kept `low` — high-quality generation is much
slower and risks the serverless function timeout mid-game. Reference art is made once,
offline, so it can afford `high`; leave `OPENAI_IMAGE_QUALITY=low` in `.env` for play.

Each target is assigned to at most **two groups** during play
(`MAX_GROUPS_PER_REFERENCE` in `app/storage.py`); once all fifteen are used twice, the
least-used target is reused so a large turnout never blocks a group.

#### Recovering a wiped Storage bucket

If the reference images vanish from Supabase Storage (or get uploaded to the wrong
paths), re-upload the committed local copies — **no regeneration, no AI cost**:

```bash
python scripts/prepare_reference.py --upload-only
```

The app serves each target from the **flat** path `references/<id>.png`. Dragging the
local `references/` folder into the Supabase dashboard instead creates
`references/<id>/image.png`, which nothing reads — every target then 404s. `--upload-only`
puts the files where the app expects them and refreshes `details.json`.

> **This does not self-heal on its own.** `storage.ensure_reference_uploaded()` returns
> early whenever `details.json` already records a `public_url`, so a running app keeps
> serving the dead link instead of re-uploading. You must run `--upload-only` explicitly.

Create your own from 10 details:

```bash
python scripts/prepare_reference.py --id my-scene \
  --details "a red umbrella" "a black cat" "heavy rain" "a yellow taxi" \
            "a streetlamp" "a puddle" "a blue mailbox" "a raincoat" \
            "cobblestones" "a foggy sky"
```

### 5. Run

```bash
uvicorn app.main:app --reload
```

Open <http://localhost:8000> (if port 8000 is unavailable — some Windows setups
reserve it — start with `uvicorn app.main:app --reload --port 8137`). Open the
leaderboard on a second screen or phone at `/leaderboard` — it updates live as
groups finish.

## AI providers

All providers are pluggable and use ordered **fallback**: each provider named in
`PROVIDER_ORDER` is tried in turn, and on any error/rate-limit/quota the next is
tried automatically. The keyless mock is appended last when `ENABLE_MOCK=true`,
so the app always has a working provider.

- **Images** — `OpenAIImageProvider` (`gpt-image-1-mini`) and/or
  `GeminiImageProvider` → `MockImageProvider`.
- **Judge + similarity** — OpenAI (`gpt-4o-mini`) and/or Gemini → mock.
- Gemini uses the current **`google-genai`** SDK (the older `google-generativeai`
  package is end-of-life and can't do image `response_modalities`).
- Model ids are env-overridable since providers rename models. Defaults:
  image `gemini-2.5-flash-image` / `gpt-image-1-mini`; vision `gemini-2.0-flash` /
  `gpt-4o-mini`.
- **Cost/speed:** the OpenAI image model defaults to **`gpt-image-1-mini`** at
  **`OPENAI_IMAGE_QUALITY=low`** — the cheapest and fastest combo (lower quality =
  fewer image tokens = less generation time). Set `OPENAI_IMAGE_QUALITY=medium`
  (or `OPENAI_IMAGE_MODEL=gpt-image-1`) for nicer images at more cost/time.
- Set `ENABLE_MOCK=false` to make an all-providers-failed step surface a clear,
  retryable error instead of falling back to the mock.

### Gemini vs OpenAI

This project ships with `PROVIDER_ORDER=openai`. Gemini **image generation is a
paid feature**: on a free-tier (no-billing) Google project the API returns `429`
with `limit: 0` for the image models, so image calls can never succeed there (and
some text model ids are gated off for newer accounts). To use Gemini, enable
**billing** on the Google Cloud project behind your `GEMINI_API_KEY`, then set
`PROVIDER_ORDER=gemini,openai`. Until then OpenAI handles all image generation and
judging — which is the configuration this project was verified end-to-end on.

## Tests

Logic tests run fully offline (no Supabase, no AI keys — they use the mock):

```bash
python -m pytest
```

They cover the game state machine (regenerate/forfeit rules, player labels),
the provider fallback chain, the mock provider/judge, scoring, and a full
in-memory end-to-end relay.

## Project layout

```
app/            FastAPI app, game state machine, storage, scoring, providers/
templates/      Jinja2 pages: index, round, reveal, leaderboard, error
static/         css + timer.js, game.js, leaderboard.js (realtime)
references/     committed reference pool (image.png + details.json per reference)
scripts/        prepare_reference.py
supabase/       schema.sql (tables, RLS, realtime, bucket)
tests/          offline pytest suite
```

## Notes

- API keys are read only from environment variables and never committed
  (`.env` is git-ignored).
- The FastAPI server is the only writer and uses the service-role key; the
  browser uses the anon key solely for read-only realtime leaderboard updates.
- Committed references store `public_url: null`; the app uploads each reference to
  the **current** project's Storage on first use, so the pool works against any
  Supabase project.
- Verified end-to-end against a live Supabase + OpenAI: game create → 3-step relay
  → real image generation → AI judge → leaderboard row → live realtime update.
