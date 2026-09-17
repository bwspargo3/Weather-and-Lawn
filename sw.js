// Minimal app-shell cache so the dashboard still opens (with stale data)
// when offline. Live weather/soil data always requires a network request
// and is intentionally NOT cached here.
const CACHE = "ground-conditions-v1";
const SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e=>{
  e.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", e=>{
  const url = new URL(e.request.url);
  // Only handle same-origin app-shell requests; let all API calls
  // (weather.gov, open-meteo, nominatim) go straight to the network.
  if(url.origin !== self.location.origin) return;
  e.respondWith(
    caches.match(e.request).then(cached=>{
      const network = fetch(e.request).then(resp=>{
        if(resp && resp.ok){
          const copy = resp.clone();
          caches.open(CACHE).then(c=>c.put(e.request, copy));
        }
        return resp;
      }).catch(()=>cached);
      return cached || network;
    })
  );
});
