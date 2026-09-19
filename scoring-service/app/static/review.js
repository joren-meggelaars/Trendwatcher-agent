/* /admin/review: give a 👍/👎 (or skip) on stored articles, one card at a time.
   Buttons and keys (y/→ 👍, n/← 👎, s/↓ skip, u undo) remove the card without a
   reload; an empty list triggers a rescore. Without JavaScript the forms work
   as plain posts. */
(function () {
  "use strict";

  var cards = document.getElementById("cards");
  if (!cards) return;

  var CATEGORY = cards.dataset.category;
  var empty = document.getElementById("empty");
  var LABELS = { like: "👍", dislike: "👎", skip: "⏭ Overgeslagen" };
  var last = null; // the last answer, for undo: {feedbackId, node, text}

  function fields(action) {
    var data = { category: CATEGORY };
    if (action) data.action = action;
    return data;
  }
  function first() { return cards.querySelector(".item-card"); }

  function showUndo() {
    if (!last) return;
    var undone = last;
    window.TW.toast(undone.text, { label: "Ongedaan maken (u)", run: undo });
  }

  function finishedIfEmpty() {
    if (first()) { empty.hidden = true; return; }
    empty.hidden = false;
    empty.textContent = "Lijst leeg, scores worden bijgewerkt…";
    window.TW.post("/admin/review/rescore", fields()).then(function (result) {
      window.location.href = "/admin/review?category=" + encodeURIComponent(CATEGORY) + "&updated=" + result.updated;
    });
  }

  function act(card, action) {
    if (card.classList.contains("busy")) return;
    card.classList.add("busy");
    window.TW.post("/admin/review/" + card.dataset.id, fields(action))
      .then(function (result) {
        if (!result.ok) { card.classList.remove("busy"); return; }
        var title = card.querySelector(".item-title").textContent.trim();
        last = {
          feedbackId: result.created ? result.feedback_id : null,
          node: card,
          text: LABELS[action] + " " + (title.length > 55 ? title.slice(0, 52) + "…" : title)
        };
        card.remove();
        showUndo();
        finishedIfEmpty();
      })
      .catch(function () { card.classList.remove("busy"); });
  }

  function undo() {
    if (!last) return;
    var undone = last;
    last = null;
    var restore = function () {
      undone.node.classList.remove("busy");
      cards.insertBefore(undone.node, first());
      empty.hidden = true;
    };
    if (undone.feedbackId === null) { restore(); return; }
    window.TW.post("/admin/review/undo/" + undone.feedbackId, fields()).then(restore);
  }

  cards.addEventListener("submit", function (event) {
    event.preventDefault();
    var card = event.target.closest(".item-card");
    if (card && event.submitter) act(card, event.submitter.value);
  });

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    var key = event.key.toLowerCase();
    if (key === "u") { event.preventDefault(); undo(); return; }
    var card = first();
    if (!card) return;
    if (key === "y" || event.key === "ArrowRight") { event.preventDefault(); act(card, "like"); }
    else if (key === "n" || event.key === "ArrowLeft") { event.preventDefault(); act(card, "dislike"); }
    else if (key === "s" || event.key === "ArrowDown") { event.preventDefault(); act(card, "skip"); }
  });

  // A 👍/👎 from an old mail link that had no digest page: record it here.
  var pending = window.TW.pendingVote();
  if (pending) {
    window.TW.post("/admin/vote/" + pending.item, { action: pending.action }).then(function (result) {
      if (!result.ok) return;
      var card = cards.querySelector('.item-card[data-id="' + pending.item + '"]');
      if (card) card.remove();
      window.TW.toast((result.vote === "like" ? "👍" : "👎") + " vastgelegd", {
        label: "Ongedaan maken",
        run: function () { window.TW.post("/admin/vote/" + pending.item, { action: result.previous || "clear" }); }
      });
    });
  }
})();
