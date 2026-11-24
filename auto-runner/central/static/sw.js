/* auto-runner central: service worker for browser push (queue item N4). Served at /sw.js with scope "/".
   The push payload is JSON: {"title": "...", "body": "...", "link": "/path"}. Links are same-origin only: anything
   pointing elsewhere opens /notifications instead. */
"use strict";

var FALLBACK = "/notifications";

function sameOrigin(link) {
  try {
    var url = new URL(link || FALLBACK, self.location.origin);
    return url.origin === self.location.origin ? url.href : new URL(FALLBACK, self.location.origin).href;
  } catch (err) {
    return new URL(FALLBACK, self.location.origin).href;
  }
}

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) {
  var data = {};
  if (event.data) {
    try { data = event.data.json() || {}; } catch (err) { data = {body: event.data.text()}; }
  }
  var title = typeof data.title === "string" && data.title ? data.title : "auto-runner";
  var options = {
    body: typeof data.body === "string" ? data.body : "",
    data: {link: sameOrigin(typeof data.link === "string" ? data.link : FALLBACK)},
    tag: typeof data.tag === "string" ? data.tag : undefined
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = sameOrigin(event.notification.data && event.notification.data.link);
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url === target && "focus" in list[i]) return list[i].focus();
    }
    return self.clients.openWindow(target);
  }));
});
