/* auto-runner central: small enhancements. Every page works without this file (CSP: script-src 'self'). */
(function () {
  "use strict";

  /* Copy buttons: <button data-copy="input-id"> copies that field and says "Copied". */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var field = document.getElementById(b.getAttribute("data-copy"));
    if (!field) return;
    var done = function () { b.textContent = "Copied"; b.classList.add("copied"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(field.value).then(done, function () { field.select(); });
    } else { field.select(); try { document.execCommand("copy"); done(); } catch (err) { /* selected for the person */ } }
  });

  /* Confirmations: a form with data-confirm opens a dialog first; confirming posts it with confirm=1. Without this
     file the server shows its own confirm page instead. */
  var dialog = null;
  function confirmDialog(form) {
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.className = "confirm-dialog";
      dialog.innerHTML = '<h2></h2><p></p><div class="row-actions"><button type="button" class="btn" value="cancel">Keep it as it is</button>' +
        '<button type="button" class="btn danger-solid" value="ok"></button></div>';
      document.body.appendChild(dialog);
      dialog.addEventListener("click", function (e) {
        var v = e.target.value;
        if (v === "cancel") dialog.close();
        if (v === "ok" && dialog.form) {
          var f = dialog.form, hidden = document.createElement("input");
          hidden.type = "hidden"; hidden.name = "confirm"; hidden.value = "1";
          f.appendChild(hidden); dialog.close(); f.submit();
        }
      });
    }
    dialog.form = form;
    dialog.querySelector("h2").textContent = form.getAttribute("data-confirm");
    dialog.querySelector("p").textContent = form.getAttribute("data-confirm-detail") || "";
    dialog.querySelector('[value="ok"]').textContent = form.getAttribute("data-confirm-action") || "Confirm";
    dialog.showModal();
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.hasAttribute("data-confirm") || f.querySelector('input[name="confirm"]')) return;
    if (typeof HTMLDialogElement === "undefined") return;           /* old browser: the server page asks instead */
    e.preventDefault();
    confirmDialog(f);
  }, true);

  /* Relative times: <time data-ago datetime="..."> stays "12 min ago" while the page is open. */
  function ago(t) {
    var s = (Date.now() - Date.parse(t)) / 1000;
    if (isNaN(s) || s < 0) return null;            /* a future time ("in 3 days") keeps the server's words */
    if (s < 45) return "just now";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    if (s < 7 * 86400) return Math.round(s / 86400) + " days ago";
    return null;
  }
  function tick() {
    document.querySelectorAll("time[data-ago]").forEach(function (el) {
      var text = ago(el.getAttribute("datetime"));
      if (text) el.textContent = text;
    });
    var up = document.querySelector("[data-updated]");
    if (up) {
      var s = Math.round((Date.now() - loaded) / 1000);
      up.textContent = s < 5 ? "Updated just now" : "Updated " + (s < 60 ? s + " s" : Math.round(s / 60) + " min") + " ago";
    }
  }
  var loaded = Date.now();
  setInterval(tick, 15000);

  /* Auto-save: a <select data-autosave> posts its form when changed; its Save button is only for pages without JS. */
  document.documentElement.classList.add("js");
  document.querySelectorAll("select[data-autosave]").forEach(function (sel) {
    sel.addEventListener("change", function () { if (sel.form) sel.form.submit(); });
  });

  /* Passwords: a Show button next to the field, and a live checklist (length, both the same). The server still decides. */
  document.querySelectorAll("[data-show-password]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var field = document.getElementById(btn.getAttribute("data-show-password"));
      if (!field) return;
      var show = field.type === "password";
      field.type = show ? "text" : "password";
      btn.textContent = show ? "Hide" : "Show";
    });
  });
  document.querySelectorAll("form[data-pw-form]").forEach(function (form) {
    var pw = form.querySelector("[data-pw-new]"), again = form.querySelector("[data-pw-again]");
    if (!pw || !again) return;
    function check() {
      var rules = {length: pw.value.length >= 12, match: pw.value !== "" && pw.value === again.value};
      form.querySelectorAll(".pw-checks [data-rule]").forEach(function (li) {
        li.classList.toggle("ok", !!rules[li.getAttribute("data-rule")]);
      });
    }
    pw.addEventListener("input", check); again.addEventListener("input", check); check();
  });

  /* Countdown: <span data-countdown="ISO time"> shows "7:42 left", then that the code expired. */
  document.querySelectorAll("[data-countdown]").forEach(function (el) {
    var end = Date.parse(el.getAttribute("data-countdown"));
    if (isNaN(end)) return;
    function step() {
      var s = Math.round((end - Date.now()) / 1000);
      if (s <= 0) { el.textContent = "expired: start the sign-in again in Claude"; el.classList.add("expired"); return; }
      el.textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2) + " left";
      setTimeout(step, 1000);
    }
    step();
  });

  /* Auto-refresh: <body data-refresh="30"> reloads while the tab is visible and nobody is typing or deciding. */
  var every = parseInt(document.body.getAttribute("data-refresh") || "0", 10);
  if (every > 0) {
    setInterval(function () {
      var a = document.activeElement;
      var busy = a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
      if (document.visibilityState !== "visible" || busy || document.querySelector("dialog[open], details[open] form")) return;
      window.location.reload();
    }, every * 1000);
  }
})();
