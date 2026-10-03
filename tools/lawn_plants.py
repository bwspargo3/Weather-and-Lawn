"""Plant-care schedule: the plants chosen in lawn-config.json × plant-catalog.json tasks,
with start dates moved by the weather run (lawn-state.json "plants") when a task has a trigger."""
import datetime as dt
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "plant-catalog.json"
CONFIG = ROOT / "lawn-config.json"
PICKER_URL = "https://bwspargo3.github.io/Weather-and-Lawn/lawn-plants.html"


def catalog():
    return {p["id"]: p for p in json.loads(CATALOG.read_text(encoding="utf-8"))["plants"]}


def chosen():
    return json.loads(CONFIG.read_text(encoding="utf-8")).get("plants", [])


def md(year, mmdd):
    m, d = map(int, mmdd.split("-"))
    return dt.date(year, m, d)


def tasks_for(year, state):
    """Every task for the chosen plants in one year, with weather-adjusted starts where available."""
    cat, out = catalog(), []
    for pid in chosen():
        plant = cat.get(pid)
        if not plant:
            continue
        for t in plant["tasks"]:
            wx = state.get("plants", {}).get(f"{year}:{pid}:{t['id']}")
            start = dt.date.fromisoformat(wx["start"]) if wx else md(year, t["start"])
            end = max(md(year, t["end"]), start + dt.timedelta(days=3))
            out.append(dict(key=f"{pid}-{t['id']}", plant=plant, task=t, start=start, end=end,
                            basis=wx["basis"] if wx else None))
    return out


def upcoming(today, season, state):
    """Next occurrence of each task (this season if its window is still open, otherwise next)."""
    nxt = {}
    for year in (season, season + 1):
        for t in tasks_for(year, state):
            if t["end"] >= today and t["key"] not in nxt:
                nxt[t["key"]] = t
    return sorted(nxt.values(), key=lambda t: (t["start"], t["key"]))


def render_section(text, today, state):
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
    items = upcoming(today, season, state)
    if not chosen():
        body = (f"_No plants chosen yet._ Pick yours on the **[plant picker]({PICKER_URL})**, "
                "and the Lawn Log shortcut saves the list. Pruning, feeding and planting reminders then appear in your calendar.")
    else:
        names = ", ".join(f"{catalog()[p]['emoji']} {catalog()[p]['name']}" for p in chosen() if p in catalog())
        rows = ["| When | Plant | Task | Timing |", "|---|---|---|---|"]
        for t in items:
            when = f"{t['start']:%b} {t['start'].day} – {t['end']:%b} {t['end'].day}, {t['start'].year}"
            timing = f"📡 {t['basis']}" if t["basis"] else "📅 typical window"
            rows.append(f"| {when} | {t['plant']['emoji']} {t['plant']['name'].split(' (')[0]} | "
                        f"**{t['task']['task']}**. {t['task']['note']} | {timing} |")
        body = (f"**Your plants:** {names} · [change]({PICKER_URL})\n\n" + "\n".join(rows))
    return re.sub(r"(<!-- PLANTS:START[^\n]*-->\n)(?:.*?\n)?(<!-- PLANTS:END -->)",
                  lambda m: m.group(1) + body + "\n" + m.group(2), text, count=1, flags=re.S)


def set_plants(ids):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["plants"] = ids
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
