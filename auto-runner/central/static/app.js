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

  /* A link with data-open="<id>" (and a page opened at #<id>) opens that <details>, e.g. "Create a project". */
  function openDetails(id) { var d = document.getElementById(id); if (d && d.tagName === "DETAILS") { d.open = true; d.scrollIntoView({ block: "nearest" }); } }
  document.querySelectorAll("a[data-open]").forEach(function (a) {
    a.addEventListener("click", function () { openDetails(a.getAttribute("data-open")); });
  });
  if (location.hash.length > 1) openDetails(location.hash.slice(1));

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

  /* Push on this browser. Both the Account page's <div data-push ...> and the "turn on push" bar on other pages
     (<div data-push-ask ...>, queue item A1) carry data-vapid-public-key, data-csrf-token and data-subscribe-url.
     enablePush(box) asks permission, registers /sw.js, subscribes and posts the subscription to the same-origin
     subscribe URL; it rejects with Error("permission") when the browser refuses. */
  var PUSH_BLOCKED = "This browser blocked notifications for this site. Allow them in the browser's site settings, then try again.";
  var PUSH_FAILED = "Could not turn on notifications in this browser. Reload the page and try again.";
  function pushSupported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }
  function registerWorker() { return navigator.serviceWorker.register("/sw.js", {scope: "/"}); }
  function keyBytes(b64) {
    var s = (b64 + "===".slice((b64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var raw = window.atob(s), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function sameOrigin(url) { return new URL(url, window.location.href).origin === window.location.origin; }
  function postJSON(box, url, body) {
    if (!url || !sameOrigin(url)) return Promise.reject(new Error("not same-origin"));
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": box.getAttribute("data-csrf-token") || ""},
      body: JSON.stringify(body)
    }).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r; });
  }
  function enablePush(box) {
    return Notification.requestPermission().then(function (permission) {
      if (permission !== "granted") throw new Error("permission");
      return registerWorker();
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (s) {
        return s || reg.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: keyBytes(box.getAttribute("data-vapid-public-key") || "")
        });
      });
    }).then(function (s) {
      return postJSON(box, box.getAttribute("data-subscribe-url"), s.toJSON());
    });
  }
  function pushError(err) { return err && err.message === "permission" ? PUSH_BLOCKED : PUSH_FAILED; }

  /* The Account page's section: Turn on or Turn off for this browser, hidden unless the browser supports push. */
  var pushBox = document.querySelector("[data-push]");
  if (pushBox && pushSupported()) {
    var onBtn = pushBox.querySelector("[data-push-on]"), offBtn = pushBox.querySelector("[data-push-off]");
    var actions = pushBox.querySelector("[data-push-actions]"), status = pushBox.querySelector("[data-push-status]");
    var say = function (text) { if (status) status.textContent = text; };
    var show = function (subscribed) {
      onBtn.hidden = subscribed; offBtn.hidden = !subscribed; actions.hidden = false;
    };
    var registration = registerWorker();
    registration.then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (s) { show(!!s); if (s) say("Turned on in this browser."); })
      .catch(function () { /* no service worker here: the buttons stay hidden */ });

    onBtn.addEventListener("click", function () {
      onBtn.disabled = true;
      enablePush(pushBox).then(function () {
        show(true); say("Turned on in this browser.");
      }).catch(function (err) {
        say(pushError(err));
      }).then(function () { onBtn.disabled = false; });
    });

    offBtn.addEventListener("click", function () {
      offBtn.disabled = true;
      registration.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (s) {
        if (!s) return null;
        var endpoint = s.endpoint;
        return s.unsubscribe().then(function () { return postJSON(pushBox, pushBox.getAttribute("data-unsubscribe-url"), {endpoint: endpoint}); });
      }).then(function () {
        show(false); say("Turned off in this browser.");
      }).catch(function () {
        say("Could not turn off notifications in this browser. Reload the page and try again.");
      }).then(function () { offBtn.disabled = false; });
    });
  }

  /* The "turn on push" bar (queue item A1): shown once per browser, only when push is supported, not blocked, not
     already subscribed here and not answered before (localStorage["push-ask"] set to "done" or "dismissed"). When
     storage cannot be read the bar stays hidden, so it never asks again on every page. */
  var askBox = document.querySelector("[data-push-ask]");
  var askRead = function () { try { return window.localStorage.getItem("push-ask"); } catch (e) { return "unavailable"; } };
  var askWrite = function (v) { try { window.localStorage.setItem("push-ask", v); } catch (e) { /* not remembered */ } };
  if (askBox && pushSupported() && Notification.permission !== "denied" && !askRead()) {
    var askOn = askBox.querySelector("[data-push-ask-on]"), askOff = askBox.querySelector("[data-push-ask-off]");
    var askStatus = askBox.querySelector("[data-push-status]");
    navigator.serviceWorker.getRegistration("/")
      .then(function (reg) { return reg ? reg.pushManager.getSubscription() : null; })
      .then(function (s) { if (!s) askBox.hidden = false; })
      .catch(function () { /* cannot tell: stay hidden */ });
    askOn.addEventListener("click", function () {
      askOn.disabled = true;
      enablePush(askBox).then(function () {
        askBox.hidden = true; askWrite("done");
      }).catch(function (err) {
        if (askStatus) askStatus.textContent = pushError(err);
      }).then(function () { askOn.disabled = false; });
    });
    askOff.addEventListener("click", function () { askBox.hidden = true; askWrite("dismissed"); });
  }

  /* Screenshots: a form with data-paste-images takes pasted or dropped images into its file input (through
     DataTransfer, so the normal multipart post sends them) and lists the chosen files. Without this file the plain
     file input still works. */
  var IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  function shotInput(form) { return form.querySelector('input[type="file"][name="file"]'); }
  function sizeText(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function listShots(form) {
    var input = shotInput(form);
    if (!input) return;
    var list = form.querySelector(".picked-files");
    if (!list) {
      list = document.createElement("ul");
      list.className = "picked-files";
      list.setAttribute("aria-live", "polite");
      input.insertAdjacentElement("afterend", list);
    }
    list.textContent = "";
    var total = 0, max = parseInt(form.getAttribute("data-max-bytes") || "0", 10);
    Array.prototype.forEach.call(input.files || [], function (f) {
      total += f.size;
      var li = document.createElement("li");
      li.textContent = f.name + " (" + sizeText(f.size) + ")";
      list.appendChild(li);
    });
    if (max && total > max) {
      var warn = document.createElement("li");
      warn.className = "picked-warn";
      warn.textContent = "Together these are " + sizeText(total) + ", over the " + sizeText(max) + " one form can send. Remove some first.";
      list.appendChild(warn);
    }
  }
  function addShots(form, files) {
    var input = shotInput(form);
    if (!input || typeof DataTransfer === "undefined") return false;
    var images = Array.prototype.filter.call(files || [], function (f) { return IMAGE_TYPES.indexOf(f.type) !== -1; });
    if (!images.length) return false;
    var dt = new DataTransfer();
    Array.prototype.forEach.call(input.files || [], function (f) { dt.items.add(f); });
    images.forEach(function (f, i) {
      if (!f.name || /^image\.\w+$/.test(f.name)) {             /* pasted images all arrive as "image.png" */
        var ext = f.type.split("/")[1].replace("jpeg", "jpg");
        f = new File([f], "pasted-" + (dt.items.length + 1) + "." + ext, { type: f.type });
      }
      dt.items.add(f);
    });
    try { input.files = dt.files; } catch (err) { return false; }
    listShots(form);
    return true;
  }
  function pasteForm(target) {
    var f = target && target.closest ? target.closest("form[data-paste-images]") : null;
    if (f) return f;
    var all = document.querySelectorAll("form[data-paste-images]");
    return all.length === 1 ? all[0] : null;                      /* one form on the page: a paste anywhere goes there */
  }
  document.addEventListener("paste", function (e) {
    var form = pasteForm(e.target);
    if (!form || !e.clipboardData) return;
    if (addShots(form, e.clipboardData.files)) e.preventDefault();  /* text pastes are left alone */
  });
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.form && t.form.hasAttribute("data-paste-images") && t === shotInput(t.form)) listShots(t.form);
  });
  document.addEventListener("dragover", function (e) {
    var form = e.target.closest && e.target.closest("form[data-paste-images]");
    if (!form || !e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") === -1) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    form.classList.add("drop-over");
  });
  document.addEventListener("dragleave", function (e) {
    var form = e.target.closest && e.target.closest("form[data-paste-images]");
    if (form && !form.contains(e.relatedTarget)) form.classList.remove("drop-over");
  });
  document.addEventListener("drop", function (e) {
    var form = e.target.closest && e.target.closest("form[data-paste-images]");
    if (!form || !e.dataTransfer) return;
    form.classList.remove("drop-over");
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      e.preventDefault();                                            /* never let the browser navigate to the image */
      addShots(form, e.dataTransfer.files);
    }
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
