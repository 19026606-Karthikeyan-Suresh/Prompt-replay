/*
 * timer.js — reusable countdown that drives the timed screens.
 *
 * It looks for an element with id="timer" carrying data attributes:
 *   data-seconds : integer countdown length (e.g. 30)
 *   data-mode    : "submit"  -> submit the form whose id is data-target
 *                  "redirect" -> navigate to the URL in data-target
 *   data-target  : form id (submit mode) or URL (redirect mode)
 *
 * The round page uses "submit" mode: it auto-submits whatever is typed when the
 * timer reaches 0 (the hard 30s cap). ("redirect" mode is supported but currently
 * unused — it navigates to a URL when the timer expires.)
 */
(function () {
    "use strict";

    // Guard against the expiry action firing more than once (timer + manual).
    var fired = false;

    /**
     * Run the configured expiry action exactly once.
     * @param {string} mode - "submit" or "redirect".
     * @param {string} target - form id or destination URL.
     */
    function runAction(mode, target) {
        if (fired) return;
        fired = true;
        if (mode === "redirect") {
            window.location.href = target;
        } else {
            var form = document.getElementById(target);
            if (form) {
                form.requestSubmit ? form.requestSubmit() : form.submit();
            }
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var el = document.getElementById("timer");
        if (!el) return;

        var remaining = parseInt(el.getAttribute("data-seconds"), 10) || 30;
        var mode = el.getAttribute("data-mode") || "submit";
        var target = el.getAttribute("data-target") || "";

        // Let other scripts (e.g. game.js) mark the action as already handled by
        // flipping this flag, so a manual submit cancels the auto-submit.
        el.__cancelAuto = function () { fired = true; };

        el.textContent = remaining;

        var tick = setInterval(function () {
            remaining -= 1;
            el.textContent = Math.max(remaining, 0);

            // Visual urgency cues as time runs low.
            if (remaining <= 5) {
                el.classList.add("danger");
            } else if (remaining <= 10) {
                el.classList.add("warn");
            }

            if (remaining <= 0) {
                clearInterval(tick);
                runAction(mode, target);
            }
        }, 1000);
    });
})();
