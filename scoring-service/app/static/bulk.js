/* Rows with a checkbox and a bar of actions that apply to the ticked ones.
   <form data-bulk> holds the buttons (data-bulk-action) and the counter
   (data-bulk-count); the checkboxes (data-bulk-row, data-bulk-all) belong to it
   through their form= attribute, so they can sit inside the table. Without
   JavaScript the buttons are simply disabled: nothing is submitted by accident. */
(function () {
  "use strict";

  var form = document.querySelector("form[data-bulk]");
  if (!form) return;

  var rows = Array.prototype.slice.call(document.querySelectorAll("[data-bulk-row]"));
  var all = document.querySelector("[data-bulk-all]");
  var actions = Array.prototype.slice.call(form.querySelectorAll("[data-bulk-action]"));
  var count = form.querySelector("[data-bulk-count]");
  var last = null; // last ticked row, for shift-click ranges

  function selected() {
    return rows.filter(function (box) { return box.checked; });
  }

  function refresh() {
    var n = selected().length;
    count.textContent = n === 0 ? "Niets geselecteerd" : n + " geselecteerd";
    form.classList.toggle("has-selection", n > 0);
    actions.forEach(function (button) { button.disabled = n === 0; });
    if (all) {
      all.checked = n > 0 && n === rows.length;
      all.indeterminate = n > 0 && n < rows.length;
    }
  }

  if (all) {
    all.addEventListener("change", function () {
      rows.forEach(function (box) { box.checked = all.checked; });
      refresh();
    });
  }

  rows.forEach(function (box) {
    box.addEventListener("click", function (event) {
      // Shift-click ticks (or unticks) everything between this row and the previous click.
      if (event.shiftKey && last && last !== box) {
        var from = rows.indexOf(last), to = rows.indexOf(box);
        rows.slice(Math.min(from, to), Math.max(from, to) + 1).forEach(function (other) { other.checked = box.checked; });
      }
      last = box;
      refresh();
    });
  });

  // Ask before changing many at once; say what will happen.
  form.addEventListener("submit", function (event) {
    var n = selected().length;
    if (n === 0) { event.preventDefault(); return; }
    var chosen = event.submitter && event.submitter.value;
    if (n > 1 && !window.confirm(n + " bronnen op “" + chosen + "” zetten?")) event.preventDefault();
  });

  refresh(); // the browser may restore ticks after a reload or back
})();
