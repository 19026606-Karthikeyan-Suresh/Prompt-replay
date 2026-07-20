/*
 * leaderboard.js — live leaderboard via Supabase realtime.
 *
 * Loaded as an ES module. It imports supabase-js from a CDN, seeds its row list
 * from the JSON the server embedded, and subscribes to INSERTs on the public
 * `leaderboard` table using the ANON key only (read-only). Each new result is
 * merged in, re-ranked (detail_score desc, similarity desc, created_at asc), and
 * re-rendered — so a second screen or phone updates the instant a group finishes.
 *
 * If the CDN import or realtime connection fails, the server-rendered rows stay
 * on screen, so the board degrades gracefully.
 */
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// Public, read-only credentials injected by the server template.
const config = window.__SUPABASE__ || {};

// Current known rows, seeded from the server-embedded JSON.
let rows = [];
try {
    const raw = document.getElementById("initial-rows").textContent;
    rows = JSON.parse(raw) || [];
} catch (e) {
    rows = [];
}

/**
 * Compare two rows for ranking: higher detail score first, then higher
 * similarity, then earlier finish time.
 * @param {object} a - A leaderboard row.
 * @param {object} b - A leaderboard row.
 * @returns {number} Standard comparator result (-1/0/1).
 */
function rankCompare(a, b) {
    if (b.detail_score !== a.detail_score) return b.detail_score - a.detail_score;
    const simA = Number(a.similarity) || 0;
    const simB = Number(b.similarity) || 0;
    if (simB !== simA) return simB - simA;
    return new Date(a.created_at) - new Date(b.created_at);
}

/**
 * Escape a string for safe insertion as HTML text.
 * @param {string} value - Untrusted text (e.g. a group name).
 * @returns {string} HTML-escaped text.
 */
function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
}

// Medal glyph + label per podium tier (index 0 = top score). Mirrors
// _PODIUM_MEDALS in app/main.py — keep the two in sync.
const MEDALS = ["🥇", "🥈", "🥉"];
const LABELS = ["Gold", "Silver", "Bronze"];

/**
 * Score key that defines a medal tier: detail score + displayed similarity %.
 * Mirrors `_tier_key()` in app/main.py; equal keys share a tier.
 * @param {object} r - A leaderboard row.
 * @returns {string} A stable tier key.
 */
function tierKey(r) {
    return r.detail_score + ":" + Math.round((Number(r.similarity) || 0) * 100);
}

/**
 * Group rank-ordered rows into up to `count` medal tiers. Consecutive rows
 * sharing a `tierKey` form one tier, so every group tied at a score is featured
 * together. Mirrors `top_tiers()` in app/main.py.
 * @param {object[]} sortedRows - Rows already sorted by rankCompare.
 * @param {number} [count=3] - Maximum number of tiers.
 * @returns {object[]} Tiers: { medal, label, groups: [] }.
 */
function topTiers(sortedRows, count) {
    const max = count || 3;
    const tiers = [];
    for (const r of sortedRows) {
        const key = tierKey(r);
        const last = tiers[tiers.length - 1];
        if (last && last.key === key) {
            last.groups.push(r);
        } else if (tiers.length < max) {
            tiers.push({ key: key, medal: MEDALS[tiers.length], label: LABELS[tiers.length], groups: [r] });
        } else {
            break;
        }
    }
    return tiers;
}

/**
 * Render the winners podium as medal tiers from the already-sorted `rows`.
 * Mirrors the server-rendered markup (and header wording) in leaderboard.html.
 * Hidden when there are no rows.
 * @param {string|null} highlightId - id of a group card to flash as newly added.
 */
function renderPodium(highlightId) {
    const podium = document.getElementById("podium");
    if (!podium) return;

    const tiers = topTiers(rows, 3);
    podium.style.display = tiers.length ? "" : "none";
    podium.innerHTML = tiers
        .map(function (tier, i) {
            const label = tier.groups.length > 1
                ? "Tied for " + tier.label + " · " + tier.groups.length + " groups"
                : tier.label;
            const cards = tier.groups
                .map(function (g) {
                    const pct = Math.round((Number(g.similarity) || 0) * 100);
                    const thumb = g.final_image_url
                        ? '<img class="podium-thumb" src="' + escapeHtml(g.final_image_url) + '" alt="" />'
                        : "";
                    const flash = g.id && g.id === highlightId ? " row-new" : "";
                    return (
                        '<div class="podium-place' + flash + '">' +
                        thumb +
                        '<div class="podium-name">' + escapeHtml(g.group_name) + "</div>" +
                        '<span class="pill">' + escapeHtml(g.detail_score) + " / 10</span>" +
                        '<div class="podium-sim">' + pct + "% similar</div>" +
                        "</div>"
                    );
                })
                .join("");
            return (
                '<div class="podium-tier tier-' + (i + 1) + '">' +
                '<div class="tier-head"><span class="tier-medal">' + tier.medal + "</span>" +
                '<span class="tier-label">' + escapeHtml(label) + "</span></div>" +
                '<div class="tier-groups">' + cards + "</div>" +
                "</div>"
            );
        })
        .join("");
}

/**
 * Render the current `rows` into the table body, ranked.
 * @param {string|null} highlightId - id of a row to flash as newly added.
 */
function render(highlightId) {
    const body = document.getElementById("board-body");
    const emptyState = document.getElementById("empty-state");
    if (!body) return;

    rows.sort(rankCompare);
    renderPodium(highlightId);
    body.innerHTML = rows
        .map(function (r, i) {
            const pct = Math.round((Number(r.similarity) || 0) * 100);
            const thumb = r.final_image_url
                ? '<img class="thumb" src="' + escapeHtml(r.final_image_url) + '" alt="" />'
                : "";
            const flash = r.id && r.id === highlightId ? " row-new" : "";
            return (
                '<tr class="' + flash.trim() + '">' +
                '<td class="rank">' + (i + 1) + "</td>" +
                "<td>" + thumb + "</td>" +
                "<td>" + escapeHtml(r.group_name) + "</td>" +
                '<td><span class="pill">' + escapeHtml(r.detail_score) + " / 10</span></td>" +
                "<td>" + pct + "%</td>" +
                "</tr>"
            );
        })
        .join("");

    if (emptyState) {
        emptyState.style.display = rows.length ? "none" : "block";
    }
}

// Render once from seed data so the JS-owned table matches the server output.
render(null);

// Wire up realtime only when we have credentials to do so.
if (config.url && config.anonKey) {
    const supabase = createClient(config.url, config.anonKey);
    supabase
        .channel("public:leaderboard")
        .on(
            "postgres_changes",
            { event: "INSERT", schema: "public", table: "leaderboard" },
            function (payload) {
                const row = payload.new;
                // Ignore duplicates in case a row is delivered more than once.
                if (!rows.some(function (r) { return r.id === row.id; })) {
                    rows.push(row);
                    render(row.id);
                }
            }
        )
        .subscribe();
}
