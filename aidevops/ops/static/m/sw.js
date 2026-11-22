// Service worker for the AIDevOps phone app: receives push messages and shows them as notifications.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});  // present so Chrome treats the page as an installable app

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data.json(); } catch (e) { d = {title: "AIDevOps", body: event.data ? event.data.text() : ""}; }
  event.waitUntil(self.registration.showNotification(d.title || "AIDevOps", {
    body: d.body || "", tag: d.tag, renotify: true, icon: "icon-192.png", badge: "icon-192.png",
    data: {url: d.url || "/m/"}, requireInteraction: d.kind === "approval",
  }));
});

// Tapping a notification opens the app on that incident. Approving is a deliberate second step inside the app,
// after reading the diff, never a button on the notification itself.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/m/";
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then((wins) => {
    for (const w of wins) if ("focus" in w) { w.navigate(url); return w.focus(); }
    return self.clients.openWindow(url);
  }));
});
