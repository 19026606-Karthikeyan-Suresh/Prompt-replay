/*
 * game.js — round-page niceties layered on top of timer.js.
 *
 * On submit (manual click OR the timer's auto-submit) we:
 *   * lock the textarea so no more typing is possible once time is up (req 2),
 *   * show a full-screen "generating" overlay that echoes the player's prompt
 *     (req 1) — it stays up during the blocking POST and disappears on its own
 *     when the next round page loads,
 *   * disable the button and cancel the timer's duplicate auto-submit.
 * The form still does a normal POST; the server generates the image and
 * redirects to the next screen.
 */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("prompt-form");
        var textarea = document.getElementById("prompt");
        var timerEl = document.getElementById("timer");
        var overlay = document.getElementById("gen-overlay");
        var promptEcho = document.getElementById("gen-prompt-echo");
        var button = form ? form.querySelector("button[type=submit]") : null;
        var originalButtonText = button ? button.textContent : "";

        if (textarea) {
            textarea.focus();
        }
        if (!form) return;

        var submitted = false; // the native POST is only triggered once

        form.addEventListener("submit", function () {
            if (submitted) return;
            submitted = true;

            // Cancel the timer's pending auto-submit so we never post twice.
            if (timerEl && typeof timerEl.__cancelAuto === "function") {
                timerEl.__cancelAuto();
            }

            // Requirement 2: once submitted (including on timer expiry) the
            // player can no longer type. readOnly (NOT disabled) keeps the value
            // in the POST body.
            if (textarea) {
                textarea.readOnly = true;
            }

            if (button) {
                button.disabled = true;
                button.textContent = "Submitting…";
            }

            // Requirement 1: show the generating animation, echoing the prompt.
            if (overlay) {
                if (promptEcho) {
                    var text = textarea ? textarea.value.trim() : "";
                    if (text) {
                        promptEcho.textContent = "“" + text + "”";
                        promptEcho.hidden = false;
                    } else {
                        promptEcho.hidden = true;
                    }
                }
                overlay.hidden = false;
            }
        });

        // If the browser restores this page from its back/forward cache (e.g. the
        // player hit Back after submitting), it would otherwise reappear frozen in
        // the "generating" state with the form locked. Reset the UI on restore so
        // the round is usable again.
        window.addEventListener("pageshow", function (event) {
            if (!event.persisted) return;
            submitted = false;
            if (overlay) {
                overlay.hidden = true;
            }
            if (textarea) {
                textarea.readOnly = false;
            }
            if (button) {
                button.disabled = false;
                button.textContent = originalButtonText;
            }
        });
    });
})();
