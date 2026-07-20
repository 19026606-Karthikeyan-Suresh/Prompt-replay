/*
 * leaderboard.js — live leaderboard via Supabase realtime.
 *
 * Loaded as an ES module. It imports supabase-js from a CDN, seeds its row list
 * from the JSON the server embedded, and subscribes to INSERTs on the public
 * `leaderboard` table using the ANON key only (read-only). Each new result is
 * merged in, re-ranked (by the displayed score — similarity desc, then created_at
 * asc), and re-rendered — so a second screen or phone updates the instant a group
 * finishes.
 *
 * Clicking any group (a podium card or a table row) opens a modal with that
 * group's per-detail breakdown, fetched from GET /game/{id}/breakdown, plus a
 * button to download the group's final image.
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
 * The single displayed percentage for a row (detail-based score).
 * @param {object} r - A leaderboard row.
 * @returns {number} The whole-number percentage shown to players.
 */
function pctOf(r) {
    return Math.round((Number(r.similarity) || 0) * 100);
}

/**
 * Compare two rows for ranking: higher displayed score (similarity, which encodes
 * the detail-based percentage) first, then earlier finish time. Mirrors the server
 * ordering in storage.list_leaderboard.
 * @param {object} a - A leaderboard row.
 * @param {object} b - A leaderboard row.
 * @returns {number} Standard comparator result (-1/0/1).
 */
function rankCompare(a, b) {
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
// _PODIUM_MEDALS in app/main.py — keep the two in sync. Ranks beyond bronze have
// no medal glyph; the podium shows the rank number instead.
const MEDALS = ["🥇", "🥈", "🥉", "", ""];
const LABELS = ["1st", "2nd", "3rd", "4th", "5th"];

/**
 * Score key that defines a podium tier: the displayed percentage.
 * Mirrors `_tier_key()` in app/main.py; equal keys share a tier.
 * @param {object} r - A leaderboard row.
 * @returns {number} A stable tier key (the whole-number percentage).
 */
function tierKey(r) {
    return pctOf(r);
}

/**
 * Group rank-ordered rows into up to `count` podium tiers. Consecutive rows
 * sharing a `tierKey` form one tier, so every group tied at a score is featured
 * together. Mirrors `top_tiers()` in app/main.py.
 * @param {object[]} sortedRows - Rows already sorted by rankCompare.
 * @param {number} [count=5] - Maximum number of tiers.
 * @returns {object[]} Tiers: { medal, label, groups: [] }.
 */
function topTiers(sortedRows, count) {
    const max = count || 5;
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
 * Render the winners podium as up to five pedestal tiers from the sorted `rows`.
 * Mirrors the server-rendered markup (and header wording) in leaderboard.html.
 * Hidden when there are no rows.
 * @param {string|null} highlightId - id of a group card to flash as newly added.
 */
function renderPodium(highlightId) {
    const podium = document.getElementById("podium");
    if (!podium) return;

    const tiers = topTiers(rows, 5);
    podium.style.display = tiers.length ? "" : "none";
    podium.innerHTML = tiers
        .map(function (tier, i) {
            const label = tier.groups.length > 1
                ? "Tied for " + tier.label + " · " + tier.groups.length + " groups"
                : tier.label;
            const cards = tier.groups
                .map(function (g) {
                    const thumb = g.final_image_url
                        ? '<img class="podium-thumb" src="' + escapeHtml(g.final_image_url) + '" alt="" />'
                        : "";
                    const gid = g.group_id
                        ? '<div class="podium-gid">' + escapeHtml(g.group_id) + "</div>"
                        : "";
                    const flash = g.id && g.id === highlightId ? " row-new" : "";
                    return (
                        '<div class="podium-place' + flash + '" data-game-id="' + escapeHtml(g.game_id) + '" role="button" tabindex="0">' +
                        thumb +
                        '<div class="podium-name">' + escapeHtml(g.group_name) + "</div>" +
                        gid +
                        '<div class="podium-pct">' + pctOf(g) + "%</div>" +
                        "</div>"
                    );
                })
                .join("");
            return (
                '<div class="podium-tier tier-' + (i + 1) + '">' +
                '<div class="tier-groups">' + cards + "</div>" +
                '<div class="tier-riser"><span class="tier-medal">' + (tier.medal || (i + 1)) + "</span></div>" +
                '<div class="tier-caption">' + escapeHtml(label) + "</div>" +
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
            const thumb = r.final_image_url
                ? '<img class="thumb" src="' + escapeHtml(r.final_image_url) + '" alt="" />'
                : "";
            const flash = r.id && r.id === highlightId ? " row-new" : "";
            return (
                '<tr class="lb-row' + flash + '" data-game-id="' + escapeHtml(r.game_id) + '">' +
                '<td class="rank">' + (i + 1) + "</td>" +
                "<td>" + thumb + "</td>" +
                "<td>" + escapeHtml(r.group_name) + "</td>" +
                "<td>" + escapeHtml(r.group_id) + "</td>" +
                '<td><span class="pill">' + pctOf(r) + "%</span></td>" +
                "</tr>"
            );
        })
        .join("");

    if (emptyState) {
        emptyState.style.display = rows.length ? "none" : "block";
    }
}

// --------------------------------------------------------------------------- //
// Breakdown modal
// --------------------------------------------------------------------------- //
const modal = document.getElementById("lb-modal");
const modalContent = document.getElementById("lb-modal-content");

/**
 * Trigger a browser download of an image URL. Fetches to a blob first so a
 * cross-origin Storage URL still downloads (the <a download> attribute alone is
 * ignored cross-origin).
 * @param {string} url - The image URL.
 * @param {string} filename - Suggested download filename.
 */
async function downloadImage(url, filename) {
    try {
        const resp = await fetch(url);
        const blob = await resp.blob();
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(objectUrl);
    } catch (e) {
        // Fall back to opening the image in a new tab if the fetch is blocked.
        window.open(url, "_blank");
    }
}

/** Close and clear the breakdown modal. */
function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    if (modalContent) modalContent.innerHTML = "";
}

/**
 * Fetch a group's breakdown and show it in the modal.
 * @param {string} gameId - The finished game's id.
 */
async function openBreakdown(gameId) {
    if (!modal || !modalContent || !gameId) return;
    modalContent.innerHTML = '<p class="lede">Loading…</p>';
    modal.hidden = false;
    let data;
    try {
        const resp = await fetch("/game/" + encodeURIComponent(gameId) + "/breakdown");
        if (!resp.ok) throw new Error("bad status");
        data = await resp.json();
    } catch (e) {
        modalContent.innerHTML = '<p class="lede">Could not load this group\'s breakdown.</p>';
        return;
    }

    const title = escapeHtml(data.group_name || "Group") +
        (data.group_id ? ' <span class="modal-gid">' + escapeHtml(data.group_id) + "</span>" : "");
    const verdicts = (data.verdicts || [])
        .map(function (v) {
            return (
                '<li class="verdict ' + (v.present ? "hit" : "miss") + '">' +
                '<span class="mark">' + (v.present ? "✓" : "✗") + "</span>" +
                '<span class="detail">' + escapeHtml(v.detail) + "</span>" +
                '<span class="reason">' + escapeHtml(v.reason) + "</span>" +
                "</li>"
            );
        })
        .join("");

    // Rounds that actually produced an image drive the toggle bar. Track each
    // one's original index into data.steps so a toggle maps back to its round.
    const steps = data.steps || [];
    const shown = steps
        .map(function (s, i) { return { s: s, idx: i }; })
        .filter(function (o) { return o.s.image_url; });
    // Default selection = the last round with an image (i.e. the final image).
    let selIdx = shown.length ? shown[shown.length - 1].idx : -1;
    const genSrc = selIdx >= 0 ? steps[selIdx].image_url : "";

    const targetFig =
        '<figure><div class="canvas-frame">' +
        (data.reference_image_url
            ? '<img class="canvas-img" src="' + escapeHtml(data.reference_image_url) + '" alt="Target image" />'
            : '<div class="canvas-blank">Target unavailable</div>') +
        "</div><figcaption>The target</figcaption></figure>";
    const genFig =
        '<figure><div class="canvas-frame">' +
        (genSrc
            ? '<img class="canvas-img" id="modal-gen-img" src="' + escapeHtml(genSrc) + '" alt="Generated image" />'
            : '<div class="canvas-blank">No image — every turn forfeited</div>') +
        '</div><figcaption id="modal-gen-cap"></figcaption></figure>';

    const toggleBar = shown.length
        ? '<div class="modal-toggle-bar">' +
          shown
              .map(function (o) {
                  return (
                      '<button type="button" class="modal-toggle" data-idx="' + o.idx + '">' +
                      '<img class="modal-toggle-thumb" src="' + escapeHtml(o.s.image_url) + '" alt="" />' +
                      "<span>Step " + o.s.step + "</span></button>"
                  );
              })
              .join("") +
          "</div>"
        : "";
    const download = genSrc
        ? '<button type="button" class="btn primary" id="lb-download">Download image ↓</button>'
        : "";

    modalContent.innerHTML =
        '<p class="eyebrow">Breakdown</p>' +
        "<h2>" + title + "</h2>" +
        '<div class="score-row"><div class="score-badge"><span class="score-num">' + Number(data.pct || 0) +
        '%</span><span class="score-label">of the picture recreated</span></div></div>' +
        '<div class="compare-grid">' + targetFig + genFig + "</div>" +
        toggleBar +
        download +
        '<ul class="verdict-list">' + verdicts + "</ul>";

    const genImg = document.getElementById("modal-gen-img");
    const genCap = document.getElementById("modal-gen-cap");
    const safe = String(data.group_id || data.group_name || "group").replace(/[^\w.-]+/g, "_");

    // Swap the large generated image + its caption to a chosen round.
    function selectStep(idx) {
        const s = steps[idx];
        if (!s || !s.image_url) return;
        selIdx = idx;
        if (genImg) genImg.src = s.image_url;
        if (genCap) {
            genCap.innerHTML =
                "Step " + s.step + " · " + escapeHtml(s.player) +
                (s.prompt
                    ? '<span class="prompt-quote">“' + escapeHtml(s.prompt) + "”</span>"
                    : '<span class="prompt-quote">(turn forfeited)</span>');
        }
        modalContent.querySelectorAll(".modal-toggle").forEach(function (btn) {
            btn.classList.toggle("active", Number(btn.getAttribute("data-idx")) === idx);
        });
    }

    modalContent.querySelectorAll(".modal-toggle").forEach(function (btn) {
        btn.addEventListener("click", function () {
            selectStep(Number(btn.getAttribute("data-idx")));
        });
    });
    if (selIdx >= 0) selectStep(selIdx); // set the initial caption + active toggle

    const dl = document.getElementById("lb-download");
    if (dl) {
        dl.addEventListener("click", function () {
            // Follow the toggle: download whichever image is currently shown.
            const src = genImg ? genImg.src : data.final_image_url || "";
            if (!src) return;
            const label = selIdx >= 0 ? "step" + steps[selIdx].step : "final";
            downloadImage(src, safe + "-" + label + ".png");
        });
    }
}

// Open the modal from any clicked (or Enter/Space-activated) group element.
document.addEventListener("click", function (event) {
    const el = event.target.closest("[data-game-id]");
    if (el) openBreakdown(el.getAttribute("data-game-id"));
});
document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
    if ((event.key === "Enter" || event.key === " ")) {
        const el = event.target.closest && event.target.closest(".podium-place[data-game-id], .lb-row[data-game-id]");
        if (el) {
            event.preventDefault();
            openBreakdown(el.getAttribute("data-game-id"));
        }
    }
});
if (modal) {
    // Click on the dimmed backdrop (outside the box) closes the modal.
    modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
    });
}
const closeBtn = document.getElementById("lb-modal-close");
if (closeBtn) closeBtn.addEventListener("click", closeModal);

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
