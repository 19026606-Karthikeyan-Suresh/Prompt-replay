-- Prompt Relay — Supabase schema
-- Run this in the Supabase SQL editor (or `supabase db` / psql) once per project.
--
-- Security model (per spec):
--   * The FastAPI server is the ONLY writer and uses the service-role key, which
--     bypasses Row Level Security entirely.
--   * The browser only ever uses the anon key, and only to READ the leaderboard
--     live via realtime. RLS below grants anon SELECT on `leaderboard` and
--     nothing else, so games and all writes stay server-side.

-- gen_random_uuid() comes from pgcrypto, which Supabase preinstalls.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- games: one row per play session; drives the 3-step relay state machine.
-- ---------------------------------------------------------------------------
create table if not exists public.games (
    id            uuid primary key default gen_random_uuid(),
    created_at    timestamptz not null default now(),
    group_name    text not null,
    group_id      text,                               -- event group identifier (participant-entered)
    group_size    int  not null default 3,           -- 3 or 4 players
    reference_id  text not null,                      -- assigned from the pool
    current_step  int  not null default 0,            -- 0 = not started, 1..3 = done through step
    finished      boolean not null default false,
    -- One prompt + resulting image URL per step. NULLs are meaningful:
    -- a NULL step-1 image means the base canvas is still blank (empty prompt).
    prompt_1      text,
    prompt_2      text,
    prompt_3      text,
    image_url_1   text,
    image_url_2   text,
    image_url_3   text,
    -- Final scoring, populated after step 3.
    detail_score  int,
    similarity    numeric,
    judge_result  jsonb                                -- per-detail present/reason for the reveal page
);

-- ---------------------------------------------------------------------------
-- leaderboard: append-only results surface the browser subscribes to.
-- ---------------------------------------------------------------------------
create table if not exists public.leaderboard (
    id              uuid primary key default gen_random_uuid(),
    game_id         uuid references public.games(id) on delete cascade,
    group_name      text not null,
    group_id        text,                             -- event group identifier (participant-entered)
    detail_score    int not null,
    -- The detail-based score fraction (0..1); displayed as round(similarity*100)%.
    similarity      numeric not null,
    final_image_url text,
    created_at      timestamptz not null default now()
);

-- Migration for projects created before group_id existed: add the columns
-- in-place (safe to run repeatedly; no-ops once the columns exist).
alter table public.games       add column if not exists group_id text;
alter table public.leaderboard add column if not exists group_id text;

-- Convenience ranked view (detail score desc, then similarity desc) used by the
-- server to render the initial leaderboard ordering.
create or replace view public.leaderboard_ranked as
    select *
    from public.leaderboard
    order by detail_score desc, similarity desc, created_at asc;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.games enable row level security;
alter table public.leaderboard enable row level security;

-- No policies on `games` => anon/authenticated cannot read or write it; only the
-- service-role key (which bypasses RLS) touches it. This is intentional.

-- The public leaderboard is world-readable so phones/second screens can watch it.
drop policy if exists "leaderboard public read" on public.leaderboard;
create policy "leaderboard public read"
    on public.leaderboard
    for select
    to anon, authenticated
    using (true);

-- ---------------------------------------------------------------------------
-- Realtime: publish leaderboard inserts so the live page updates instantly.
-- Wrapped in a DO block so re-running this file is idempotent.
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'leaderboard'
    ) then
        alter publication supabase_realtime add table public.leaderboard;
    end if;
end $$;

-- ---------------------------------------------------------------------------
-- Storage bucket for reference + generated images (public read).
-- The Storage tables live in the `storage` schema; this insert is safe to run
-- in the SQL editor. Alternatively create the bucket named `images` (public)
-- from the Storage UI.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('images', 'images', true)
on conflict (id) do update set public = true;
