"""Plant-care schedule: the plants chosen in lawn-config.json × plant-catalog.json care profiles.

Config "plants" entries are catalog ids ("roses") or custom plants that borrow a profile's care
({"name": "Grandma's lilac", "profile": "spring-shrub"}). Windows are relative to the location's
frost dates (lawn_engine.windows); the weather run can move a task's start (lawn-state.json "plants").
"""
import datetime as dt
import json
import re

from lawn_engine import ROOT, climate, config, windows
from lawn_links import PAGES_URL

CATALOG = ROOT / "plant-catalog.json"
CONFIG = ROOT / "lawn-config.json"
PICKER_URL = f"{PAGES_URL}/lawn-plants.html"


def catalog():
    return {p["id"]: p for p in json.loads(CATALOG.read_text(encoding="utf-8"))["plants"]}


def zone_num(clim):
    m = re.match(r"(\d+)", str(clim.get("zone", "")))
    return int(m.group(1)) if m else None


def in_zone(item, zone):
    z = item.get("zones")
    return not z or zone is None or z[0] <= zone <= z[1]


def slug(name):
    return "c-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]


def chosen_items(cfg=None):
    """[{key, name, plant (profile with display name), custom}] for the configured plants."""
    cfg = cfg or config()
    cat, out = catalog(), []
    for entry in cfg.get("plants", []):
        if isinstance(entry, str) and entry in cat:
            out.append(dict(key=entry, name=cat[entry]["name"], plant=cat[entry], custom=False))
        elif isinstance(entry, dict) and entry.get("profile") in cat:
            prof = dict(cat[entry["profile"]], name=entry["name"])
            out.append(dict(key=slug(entry["name"]), name=entry["name"], plant=prof, custom=True,
                            profile=cat[entry["profile"]]["name"]))
    return out


def tasks_for(year, state, cfg=None):
    """Every task for the chosen plants in one year (zone-filtered), with weather-adjusted starts."""
    cfg = cfg or config()
    clim = climate(cfg)
    zone = zone_num(clim)
    out = []
    for item in chosen_items(cfg):
        for t in item["plant"]["tasks"]:
            if not in_zone(t, zone):
                continue
            start, end, _ = windows(t, year, clim)
            wx = state.get("plants", {}).get(f"{year}:{item['key']}:{t['id']}")
            if wx:
                start = dt.date.fromisoformat(wx["start"])
                end = max(end, start + dt.timedelta(days=3))
            out.append(dict(key=f"{item['key']}-{t['id']}", plant=item["plant"], name=item["name"], task=t,
                            start=start, end=end, basis=wx["basis"] if wx else None))
    return out


def upcoming(today, season, state):
    nxt = {}
    for year in (season, season + 1):
        for t in tasks_for(year, state):
            if t["end"] >= today and t["key"] not in nxt:
                nxt[t["key"]] = t
    return sorted(nxt.values(), key=lambda t: (t["start"], t["key"]))


def render_section(text, today, state):
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
    items = chosen_items()
    if not items:
        body = (f"_No plants chosen yet._ Pick yours on the **[plant picker]({PICKER_URL})**, "
                "and the Lawn Log shortcut saves the list. Pruning, feeding and planting reminders then appear in your calendar.")
    else:
        names = ", ".join(f"{i['plant']['emoji']} {i['name']}" + (f" _(cared for as: {i['profile']})_" if i["custom"] else "")
                          for i in items)
        rows = ["| When | Plant | Task | Timing |", "|---|---|---|---|"]
        for t in upcoming(today, season, state):
            when = f"{t['start']:%b} {t['start'].day} – {t['end']:%b} {t['end'].day}, {t['start'].year}"
            timing = f"📡 {t['basis']}" if t["basis"] else "📅 typical for your frost dates"
            rows.append(f"| {when} | {t['plant']['emoji']} {t['name'].split(' (')[0]} | "
                        f"**{t['task']['task']}**. {t['task']['note']} | {timing} |")
        body = f"**Your plants:** {names} · [change]({PICKER_URL})\n\n" + "\n".join(rows)
    return re.sub(r"(<!-- PLANTS:START[^\n]*-->\n)(?:.*?\n)?(<!-- PLANTS:END -->)",
                  lambda m: m.group(1) + body + "\n" + m.group(2), text, count=1, flags=re.S)


def parse_plants(raw):
    """'roses, spring-shrub=Grandma's lilac' → (entries, errors)."""
    cat, entries, bad = catalog(), [], []
    for part in [p.strip() for p in raw.split(",") if p.strip() and p.strip().lower() != "none"]:
        if "=" in part:
            prof, name = (x.strip() for x in part.split("=", 1))
            if prof.lower() in cat and name:
                entries.append({"name": name[:60], "profile": prof.lower()})
            else:
                bad.append(part)
        elif part.lower() in cat:
            entries.append(part.lower())
        else:
            bad.append(part)
    uniq, seen = [], set()
    for e in entries:
        k = e if isinstance(e, str) else slug(e["name"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq, bad


def set_plants(entries):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["plants"] = entries
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
