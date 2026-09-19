/* Shared by every admin page. No inline scripts anywhere (the CSP forbids them),
   so page behaviour is switched on with data-* attributes. */
(function () {
  "use strict";

  // Mobile menu.
  var shell = document.querySelector(".shell");
  var toggle = document.querySelector("[data-nav-toggle]");
  if (shell && toggle) {
    toggle.addEventListener("click", function () { shell.classList.toggle("nav-open"); });
  }

  // <form data-confirm="Sure?"> asks before it submits.
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) event.preventDefault();
  });

  // <form data-confirm-when="s__DIGEST_DRY_RUN=false" data-confirm="..."> asks only when that field has that value.
  document.addEventListener("submit", function (event) {
    var rule = event.target.getAttribute && event.target.getAttribute("data-confirm-when");
    if (!rule) return;
    var parts = rule.split("=");
    var field = event.target.elements[parts[0]];
    if (field && field.value === parts[1] && !window.confirm(event.target.getAttribute("data-confirm-text"))) {
      event.preventDefault();
    }
  });

  var toastEl = document.getElementById("toast");
  var toastTimer = null;

  window.TW = {
    /* A short message at the bottom, optionally with one action (e.g. undo). */
    toast: function (text, action) {
      if (!toastEl) return;
      toastEl.textContent = text;
      if (action) {
        toastEl.appendChild(document.createTextNode(" "));
        var link = document.createElement("a");
        link.href = "#";
        link.textContent = action.label;
        link.addEventListener("click", function (event) {
          event.preventDefault();
          toastEl.hidden = true;
          action.run();
        });
        toastEl.appendChild(link);
      }
      toastEl.hidden = false;
      window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(function () { toastEl.hidden = true; }, action ? 9000 : 3500);
    },

    /* POST a form-encoded body and get JSON back. A 401 means the login ran
       out while the page was open: go to the login and come back here. */
    post: function (url, fields) {
      var body = new URLSearchParams(fields || {});
      return fetch(url, {
        method: "POST",
        headers: { "X-Requested-With": "fetch" },
        body: body,
        credentials: "same-origin"
      }).then(function (response) {
        if (response.status === 401) {
          window.location.href = "/admin/login?next=" + encodeURIComponent(window.location.pathname + window.location.search);
          throw new Error("login required");
        }
        return response.json();
      });
    },

    /* The ?vote=<item>:<like|dislike> from a mail link, once, then removed
       from the address so a reload does not vote again. */
    pendingVote: function () {
      var match = /^(\d+):(like|dislike)$/.exec(new URLSearchParams(window.location.search).get("vote") || "");
      if (!match) return null;
      var url = new URL(window.location.href);
      url.searchParams.delete("vote");
      window.history.replaceState(null, "", url.pathname + url.search);
      return { item: match[1], action: match[2] };
    }
  };
})();
