// Shared helpers for the Lawn Log pages (GitHub Pages). Works for any copy of the repo:
// the owner/repo come from the Pages URL, and the tracker branch from lawn-site.json.
window.LawnSite = (function () {
  const owner = location.hostname.split(".")[0];
  const repo = location.pathname.split("/").filter(Boolean)[0] || "Weather-and-Lawn";
  let branch = "main";
  const ready = fetch("lawn-site.json", {cache: "no-store"})
    .then(r => r.ok ? r.json() : {}).then(j => { if (j.branch) branch = j.branch; }).catch(() => {});
  const raw = p => `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${p}`;
  return {
    ready,
    owner, repo,
    raw,
    json: p => ready.then(() => fetch(raw(p), {cache: "no-store"})).then(r => r.json()),
    shortcut: text => "shortcuts://run-shortcut?name=" + encodeURIComponent("Lawn Log") + "&input=text&text=" + encodeURIComponent(text),
    github: text => `https://github.com/${owner}/${repo}/issues/new?title=` + encodeURIComponent(text),
    tracker: anchor => ready.then(() => `https://github.com/${owner}/${repo}/blob/${branch}/LAWN_TRACKER.md` + (anchor ? "#" + anchor : "")),
  };
})();
