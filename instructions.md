# Prompt Relay

A "prompt telephone" party game. A group tries to recreate a hidden **reference image** — defined by exactly **10 details** — by writing a chain of 30-second prompts. Each prompt edits the evolving image on screen. Only the first player ever sees the reference, so information is carried forward by memory, coaching, and the image itself. The final image is scored by how many of the 10 details survived.

## Overview

- Players work in **groups of 3–4**.
- Each group is assigned one pre-made reference image built from a fixed set of **10 details**.
- Only **player 1** sees the reference, and only for **30 seconds** at the very start. After that it is hidden until the end.
- The group takes **3 turns** ("steps"). On each turn one member (or a pair) has **30 seconds** to type a prompt; when the timer hits 0 the prompt is auto-submitted and the AI updates the image.
- At the end, the reference and its 10 details are revealed and an AI judge scores the final image out of 10.
- One group plays at a time; results go on a shared **leaderboard**.

## Setup & content preparation (done before the event)

- **Reference pool:** a set of reference images is prepared ahead of time. Each reference is stored as an image file plus a `details.json` listing its exactly **10 details** (short phrases, e.g. `"a red umbrella"`, `"a black cat"`, `"raining"`).
- References are generated with the same image AI used in-game (see *AI integration*), via `scripts/prepare_reference.py`, and saved under `references/`.
- At game start each group is **assigned one reference** from the pool.

## How to play (game flow)

1. **Group name** is entered → a new game session is created and a reference is assigned.
2. **Reference reveal (player 1 only):** the reference image is shown to player 1 for **30 seconds** with a visible countdown, then hidden for the rest of the game. During and after the reveal, player 1 may **verbally coach** teammates on what the target should contain (teammates never see the reference themselves).
3. **Step 1 — player 1:** a blank canvas is shown. Player 1 has **30 seconds** to type a prompt describing the target image. On timeout the prompt is auto-submitted and the AI **generates the base image** from it (text-to-image).
4. **Step 2 — player 2:** the image from step 1 is shown. Player 2 has **30 seconds** to type a prompt describing what to add/change. On timeout the AI **edits the current image** with that prompt (image-to-image).
5. **Step 3 — player 3 (or players 3 + 4 together):** the image from step 2 is shown, edited the same way into the **final image**.
6. There are always **3 steps total**:
   - **3-person group:** each member does one solo step.
   - **4-person group:** two members do solo steps and the remaining two **pair up** to write one prompt together within the same 30 seconds.

> Each player describes the **current on-screen image** (what to build or change next), never the original reference — only player 1 ever saw that.

## Timing rules

- Reference reveal: **30 seconds** (player 1 only).
- Each prompt: **30 seconds**, hard cap. A visible countdown drives the UI and **auto-submits** whatever is in the box when it reaches 0.
- **Empty prompt at timeout:** the current image is kept unchanged and that step is forfeited (a wasted turn). If step 1 is empty, the base image stays blank and the next non-empty prompt generates the base image instead of editing.

## Image generation mechanic

- **Step 1 = generate** (text-to-image): the first prompt creates the base image.
- **Steps 2–3 = edit** (image-to-image): each later prompt edits the current image so the image builds up coherently over the relay.

## Scoring

- After step 3, reveal to the whole group: the **reference image**, its **10 details**, and the **full relay progression** (all 3 prompts and all 3 generated images side by side).
- **Detail score (out of 10):** an AI **vision judge** compares the final image to the 10 details and returns, per detail, present/absent with a short reason. The score is the number of details visibly present.
- **Tiebreaker — image similarity:** if two groups tie on detail score, the group whose final image is **more visually similar** to the reference wins. Similarity is a 0–100 rating (see *AI integration*).

## Winner

- Highest **detail score** wins. Ties are broken by **higher similarity** to the reference.

## Leaderboard

- One group plays at a time; each result is appended to a persistent leaderboard.
- Stored per game: group name, detail score (/10), similarity %, final image, timestamp.
- The leaderboard page shows all groups ranked by **detail score (desc), then similarity (desc)**, with each group's final-image thumbnail.
- The leaderboard is stored in **Supabase (hosted Postgres)**, so it can be opened on a second screen or on players' phones via a link and updates **live** — the page subscribes to Supabase realtime with the public anon key over a read-only leaderboard view, while writes happen server-side.

## AI integration

All AI providers are pluggable and use **fallback**: try the primary provider, and on error / rate-limit / quota, automatically try the next.

- **Image generate/edit** — interface with `generate(prompt) -> image_bytes` and `edit(image_bytes, prompt) -> image_bytes`.
  - `GeminiImageProvider` (primary, free tier) and `OpenAIImageProvider` (`gpt-image-1`, fallback), wrapped by a `FallbackImageProvider` that takes an ordered list.
- **Detail judge** — `score(image_bytes, details: list[str]) -> JudgeResult` returning structured JSON (per-detail `present` + `reason`, and total). Implemented for Gemini and OpenAI with the same fallback.
- **Similarity** — `similarity(image_a, image_b) -> float` (0–1). Default implementation asks the vision model to rate visual similarity (reuses the judge providers, no heavy local ML dependency). Optional upgrade: local CLIP cosine similarity.
- **Keys** live in environment variables (`GEMINI_API_KEY`, `OPENAI_API_KEY`) loaded from `.env` — never hard-coded. Provider order is configurable in `config.py`.
- If **all** providers fail for a step, show a clear error and let the facilitator retry that step.

## Tech stack & project structure

- **Python 3.11+**, **FastAPI** + **Uvicorn**, **Jinja2** templates, vanilla HTML/CSS/JS (no build step).
- **Supabase** for persistence: **Postgres** tables for games + leaderboard, a **public-read Storage bucket** for reference and generated images (served via public URLs), and **realtime** for the live leaderboard. The FastAPI server is the only writer and uses the **service-role key** (kept server-side), so row-level-security stays simple; the browser uses the anon key only for read-only realtime.
- Dependencies: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `supabase`, `google-generativeai`, `openai`, `python-dotenv`, `pillow`. (Optional CLIP: `torch`, `open_clip_torch`.)

```
prompt-relay/
  app/
    main.py              # FastAPI app + routes
    game.py              # game session state machine (steps, ordering)
    config.py            # env keys (Supabase + AI), provider order
    storage.py           # Supabase client: Postgres rows + Storage uploads/URLs
    scoring.py           # detail judge + similarity orchestration
    providers/
      base.py            # ImageProvider, DetailJudge interfaces
      gemini.py
      openai.py
      fallback.py
  templates/             # index, reference, round, reveal, leaderboard
  static/                # css + js/timer.js, js/game.js, js/leaderboard.js (realtime)
  references/            # source reference images + details.json (uploaded to Storage)
  scripts/prepare_reference.py
  supabase/              # SQL: table definitions + RLS policies
  .env.example           # SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY, GEMINI/OPENAI keys
  requirements.txt
  README.md
```

## Screens / routes

- `GET /` — group name entry.
- `POST /game` — create game, assign a reference → redirect to reference reveal.
- `GET /game/{id}/reference` — 30s reference reveal (player 1), auto-advances to step 1.
- `GET /game/{id}/round/{n}` — canvas + 30s timer + prompt input.
- `POST /game/{id}/round/{n}` — submit prompt → generate/edit image → next step or reveal.
- `GET /game/{id}/reveal` — reference + 10 details + relay progression + detail score.
- `GET /leaderboard` — ranked results; the page subscribes to Supabase realtime for live updates.
- Reference and generated images are uploaded to the **Supabase Storage** bucket; pages reference their public URLs.
- `scripts/prepare_reference.py` — offline reference-image creation from 10 details.

## Code requirements

- Every function has a **docstring**: what it does, each argument, and what it returns.
- **Comments** explain the purpose of non-obvious variables and why they are initialized.
- API keys only via environment variables, never committed.

## Version control

- Local **git** repository (`git init`), committed incrementally.
- `.gitignore` excludes `.env` and `__pycache__/`. Game rows and images live in Supabase, not the repo; keep the curated `references/` source folder and the `supabase/` SQL committed.
