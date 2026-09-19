/* /admin/digest/<id>: vote on the items of one digest without leaving the page.
   A click sets that item's answer through POST /admin/vote/<id>; clicking the
   active button again clears it. Every change can be undone from the toast. */
(function () {
  "use strict";

  var root = document.querySelector("[data-digest]");
  if (!root) return;

  var barEl = document.getElementById("progress-bar");
  var textEl = document.getElementById("progress-text");
  var total = root.querySelectorAll(".item-card").length;
  var MESSAGE = { like: "👍 Interessant", dislike: "👎 Niet interessant", clear: "Antwoord gewist" };

  function votedCount() {
    return root.querySelectorAll('.item-card[data-vote="like"], .item-card[data-vote="dislike"]').length;
  }

  function updateProgress() {
    var voted = votedCount();
    if (textEl) textEl.textContent = voted + " van " + total + " beoordeeld";
    if (barEl) barEl.style.width = (total ? Math.round((100 * voted) / total) : 0) + "%";
  }

  function render(card, vote) {
    card.dataset.vote = vote || "";
    card.classList.toggle("voted-up", vote === "like");
    card.classList.toggle("voted-down", vote === "dislike");
    card.querySelectorAll(".vote-btn").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.action === vote));
    });
    var state = card.querySelector(".vote-state");
    if (state) state.textContent = vote === "like" ? "Interessant" : vote === "dislike" ? "Niet interessant" : "";
    updateProgress();
  }

  function shortTitle(card) {
    var title = card.querySelector(".item-title").textContent.trim();
    return title.length > 60 ? title.slice(0, 57) + "…" : title;
  }

  function vote(card, action, quiet) {
    if (card.classList.contains("busy")) return;
    card.classList.add("busy");
    window.TW.post("/admin/vote/" + card.dataset.item, { action: action })
      .then(function (result) {
        card.classList.remove("busy");
        if (!result.ok) {
          window.TW.toast("Opslaan mislukt, probeer het opnieuw.");
          return;
        }
        render(card, result.vote);
        if (quiet) return;
        var undo = { label: "Ongedaan maken", run: function () { vote(card, result.previous || "clear", true); } };
        window.TW.toast(MESSAGE[result.vote || "clear"] + ": " + shortTitle(card), undo);
      })
      .catch(function () {
        card.classList.remove("busy");
      });
  }

  root.addEventListener("click", function (event) {
    var button = event.target.closest(".vote-btn");
    if (!button) return;
    var card = button.closest(".item-card");
    var pressed = card.dataset.vote === button.dataset.action;
    vote(card, pressed ? "clear" : button.dataset.action);
  });

  updateProgress();

  // The 👍/👎 that was clicked in the mail: record it now (set, not toggle).
  var pending = window.TW.pendingVote();
  if (pending) {
    var target = root.querySelector('.item-card[data-item="' + pending.item + '"]');
    if (target) {
      target.scrollIntoView({ block: "center" });
      vote(target, pending.action);
    }
  }
})();
