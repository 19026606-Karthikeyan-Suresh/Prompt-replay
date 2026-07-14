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

/**
 * Render the current `rows` into the table body, ranked.
 * @param {string|null} highlightId - id of a row to flash as newly added.
 */
function render(highlightId) {
    const body = document.getElementById("board-body");
    const emptyState = document.getElementById("empty-state");
    if (!body) return;

    rows.sort(rankCompare);
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
