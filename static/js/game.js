/*
 * game.js — round-page niceties layered on top of timer.js.
 *
 * Responsibilities:
 *   * Keep the prompt textarea focused so players can type immediately.
 *   * Prevent a double submit: once the form is submitting (manual click OR the
 *     timer's auto-submit), disable the button and tell the timer not to fire
 *     its own submit again. The server also treats repeat steps idempotently,
 *     so this is belt-and-braces.
 */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("prompt-form");
        var textarea = document.getElementById("prompt");
        var timerEl = document.getElementById("timer");

        if (textarea) {
            textarea.focus();
        }

        if (form) {
            form.addEventListener("submit", function () {
                // Cancel the timer's pending auto-submit so we never post twice.
                if (timerEl && typeof timerEl.__cancelAuto === "function") {
                    timerEl.__cancelAuto();
                }
                var button = form.querySelector("button[type=submit]");
                if (button) {
                    button.disabled = true;
                    button.textContent = "Submitting…";
                }
            });
        }
    });
})();
