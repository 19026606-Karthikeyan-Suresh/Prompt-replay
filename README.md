# Prompt Relay

A "prompt telephone" party game. A group of 3–4 recreates a hidden **reference
image** — defined by exactly **10 details** — by writing a chain of three
30-second prompts. The first prompt generates a base image (text-to-image); the
next two edit it (image-to-image). Only **Player 1** ever sees the reference, and
only for 30 seconds. At the end an AI vision judge scores the final image out of
10, similarity breaks ties, and results land on a **live leaderboard**.

> Built with FastAPI + Jinja2 (no build step), Supabase (Postgres + Storage +
> realtime), and pluggable Gemini/OpenAI image + judge providers with automatic
> fallback. A keyless **mock provider** lets the whole game run without any AI
> keys for testing and demos.

## How a game flows

1. **Group name + size** entered → a game is created and a reference assigned.
2. **Reference reveal** (Player 1 only): 30-second countdown, then it hides.
   Player 1 coaches the team out loud but never shows them the picture.
3. **Step 1** (Player 1): 30s prompt → AI **generates** the base image.
4. **Step 2** (Player 2): 30s prompt → AI **edits** the current image.
5. **Step 3** (Player 3, or Players 3 & 4 paired in a 4-person group): final edit.
6. **Reveal**: reference, the 10 details, the full relay, and the score out of 10.
7. Result is appended to the **leaderboard** (ranked by detail score, then similarity).

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
   Storage bucket.
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

### 4. Seed the reference pool

A sample pool of three real illustrations (generated with `gpt-image-1`) is
committed under `references/`, each containing all 10 of its target details. To
(re)generate references with the configured image AI and upload them to Storage:

```bash
python scripts/prepare_reference.py --seed          # uses your AI keys, or the mock
python scripts/prepare_reference.py --seed --no-upload   # local only, no Storage
```

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

- **Images** — `OpenAIImageProvider` (`gpt-image-1`) and/or `GeminiImageProvider`
  → `MockImageProvider`.
- **Judge + similarity** — OpenAI (`gpt-4o-mini`) and/or Gemini → mock.
- Gemini uses the current **`google-genai`** SDK (the older `google-generativeai`
  package is end-of-life and can't do image `response_modalities`).
- Model ids are env-overridable since providers rename models. Defaults:
  image `gemini-2.5-flash-image` / `gpt-image-1`; vision `gemini-2.0-flash` /
  `gpt-4o-mini`.
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

They cover the game state machine (generate/edit/forfeit rules, player labels),
the provider fallback chain, the mock provider/judge, scoring, and a full
in-memory end-to-end relay.

## Project layout

```
app/            FastAPI app, game state machine, storage, scoring, providers/
templates/      Jinja2 pages: index, reference, round, reveal, leaderboard, error
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
