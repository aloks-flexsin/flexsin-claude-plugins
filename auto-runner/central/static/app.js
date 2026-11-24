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

  /* Push on this browser: the Account page's <div data-push data-vapid-public-key ...>. The buttons stay hidden unless
     this browser supports push; Turn on asks permission, registers /sw.js, subscribes and posts the subscription to
     the same-origin subscribe URL; Turn off unsubscribes here and tells the service. */
  var pushBox = document.querySelector("[data-push]");
  if (pushBox && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window) {
    var onBtn = pushBox.querySelector("[data-push-on]"), offBtn = pushBox.querySelector("[data-push-off]");
    var actions = pushBox.querySelector("[data-push-actions]"), status = pushBox.querySelector("[data-push-status]");
    var say = function (text) { if (status) status.textContent = text; };
    var keyBytes = function (b64) {
      var s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
      var raw = window.atob(s), out = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
      return out;
    };
    var sameOrigin = function (url) { return new URL(url, window.location.href).origin === window.location.origin; };
    var post = function (url, body) {
      if (!sameOrigin(url)) return Promise.reject(new Error("not same-origin"));
      return fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": pushBox.getAttribute("data-csrf-token") || ""},
        body: JSON.stringify(body)
      }).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r; });
    };
    var show = function (subscribed) {
      onBtn.hidden = subscribed; offBtn.hidden = !subscribed; actions.hidden = false;
    };
    var registration = navigator.serviceWorker.register("/sw.js", {scope: "/"});
    registration.then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (s) { show(!!s); if (s) say("Turned on in this browser."); })
      .catch(function () { /* no service worker here: the buttons stay hidden */ });

    onBtn.addEventListener("click", function () {
      onBtn.disabled = true;
      Notification.requestPermission().then(function (permission) {
        if (permission !== "granted") throw new Error("permission");
        return registration;
      }).then(function (reg) {
        return reg.pushManager.getSubscription().then(function (s) {
          return s || reg.pushManager.subscribe({
            userVisibleOnly: true, applicationServerKey: keyBytes(pushBox.getAttribute("data-vapid-public-key") || "")
          });
        });
      }).then(function (s) {
        return post(pushBox.getAttribute("data-subscribe-url"), s.toJSON());
      }).then(function () {
        show(true); say("Turned on in this browser.");
      }).catch(function (err) {
        say(err && err.message === "permission"
          ? "This browser blocked notifications for this site. Allow them in the browser's site settings, then try again."
          : "Could not turn on notifications in this browser. Reload the page and try again.");
      }).then(function () { onBtn.disabled = false; });
    });

    offBtn.addEventListener("click", function () {
      offBtn.disabled = true;
      registration.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (s) {
        if (!s) return null;
        var endpoint = s.endpoint;
        return s.unsubscribe().then(function () { return post(pushBox.getAttribute("data-unsubscribe-url"), {endpoint: endpoint}); });
      }).then(function () {
        show(false); say("Turned off in this browser.");
      }).catch(function () {
        say("Could not turn off notifications in this browser. Reload the page and try again.");
      }).then(function () { offBtn.disabled = false; });
    });
  }

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
