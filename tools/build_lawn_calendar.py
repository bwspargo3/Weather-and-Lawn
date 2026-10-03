#!/usr/bin/env python3
"""Build lawn-calendar.ics from the lawn program, LAWN_TRACKER.md statuses and lawn-state.json.

The tracker's Status Table is the source of truth: steps marked ✅ / ❌ / ➖ are dropped
from the current season, and only ⬜ steps get events. Typical dates come from the program
(programs/<id>.json) resolved against the location's frost dates; the daily weather run
(lawn-state.json) replaces them with weather-timed dates. Next season is always included.

Usage:  python3 tools/build_lawn_calendar.py [--season 2026] [--today 2026-10-02]
"""
import argparse
import datetime as dt
import json
import pathlib
import re
from zoneinfo import ZoneInfo

from lawn_engine import active_steps, climate, config, program, resolve, windows
from lawn_links import PAGES_URL, TRACKER_URL, claude_line, go_url
from lawn_plants import tasks_for

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER = ROOT / "LAWN_TRACKER.md"
OUT = ROOT / "lawn-calendar.ics"
STATE = ROOT / "lawn-state.json"  # weather-timed dates from lawn_weather.py (optional)
STAMP = "20260101T000000Z"  # fixed so unchanged events produce an identical file
DAY = dt.timedelta(days=1)


def read_statuses():
    statuses = {}
    for line in TRACKER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*([0-9A-Z]+)\s*\|\s*\[.*?\]\(#.*?\)\s*\|.*\|\s*([^|]+?)\s*\|\s*$", line)
        if m:
            statuses[m.group(1)] = m.group(2)
    return statuses


def esc(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75 if not out else 74
        while (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


def event(uid, day, title, desc, stamp):
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@weather-and-lawn",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
        f"DTEND;VALUE=DATE:{day + DAY:%Y%m%d}",
        f"SUMMARY:{esc(title)}",
        f"DESCRIPTION:{esc(desc)}",
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{esc(title)}",
        "TRIGGER:PT9H",  # 9:00 AM on the day
        "END:VALARM",
        "END:VEVENT",
    ]


def tail(*parts):
    return "\n\n".join(p for p in parts if p)


def season_events(year, today, steps, pending_ids, catch_up, state, clim):
    events = []
    where = state.get("location", {}).get("label", "your area")
    for s in steps:
        sid = s["id"]
        if sid not in pending_ids:
            continue
        start, end, buy = windows(s, year, clim)
        wx = state.get("steps", {}).get(f"{year}:{sid}")
        if wx:
            iso = lambda k: dt.date.fromisoformat(wx[k])
            start, end = iso("start"), iso("end")
            buy = iso("buy") if buy and wx.get("buy") else (start - 7 * DAY if buy else None)
        if end < today:
            continue  # window fully passed
        tag = "📡 " if wx else ""
        timing = (f"📡 Weather-timed for {where}: {wx['basis']}" if wx
                  else "📅 Typical date. It moves automatically once the weather says when." if s.get("trigger")
                  else "📅 Typical date for your area.")
        desc = tail(timing, s["note"], f"Window: {start:%b %d} – {end:%b %d}\nDetails: {TRACKER_URL}#{s['anchor']}",
                    f"✅ Done: {go_url(sid, 'done')}", f"❌ Skip: {go_url(sid, 'skip')}", claude_line())
        planned = []
        if buy:
            planned.append(("buy", buy, f"{tag}🛒 Buy {s['buy_item']} ({s['name']})"))
        planned.append(("start", start, f"{tag}🌱 {s['name']}: " + ("apply now" if wx and buy else "window opens")))
        if wx and wx.get("best"):
            planned.append(("best", dt.date.fromisoformat(wx["best"]), f"📡 ⭐ Best day: {s['name']}"))
            desc = f"⭐ {wx['best_note']}\n\n" + desc
        if s.get("lastcall") and end - 5 * DAY > start:
            planned.append(("last", end - 5 * DAY, f"{tag}⏰ Last call: {s['name']} (by {end:%b} {end.day})"))
        if wx and wx.get("repeat_every"):
            k, nxt = 0, start + dt.timedelta(days=wx["repeat_every"])
            while nxt <= dt.date.fromisoformat(wx["repeat_until"]):
                planned.append((f"r{k}", nxt, f"📡 {wx.get('repeat_title', 'Repeat: ' + s['name'])}"))
                k, nxt = k + 1, nxt + dt.timedelta(days=wx["repeat_every"])
        else:
            for i, (spec, title) in enumerate(s.get("extra", [])):
                planned.append((f"x{i}", resolve(spec, year, clim), title))

        overdue = [p for p in planned if p[1] < today and p[0] in ("buy", "start")]
        for kind, day, title in planned:
            if day >= today:
                events.append((f"{year}-{sid}-{kind}", day, title, desc))
        if catch_up and overdue:
            # First day we noticed it was overdue, so the reminder doesn't slide forward every day.
            first = state.setdefault("catchup", {}).setdefault(f"{year}:{sid}", (today + DAY).isoformat())
            events.append((f"{year}-{sid}-catchup", dt.date.fromisoformat(first),
                           f"⚠️ Now: {s['name']} (window open until {end:%b %d})", desc))
    return events


def plant_events(state, today, years):
    out, season = [], years[0]
    for year in years:
        for t in tasks_for(year, state):
            if t["end"] < today:
                continue
            p, task = t["plant"], t["task"]
            tag = "📡 " if t["basis"] else ""
            desc = tail(f"📡 {t['basis']}" if t["basis"] else "📅 Typical window for your frost dates.", task["note"],
                        f"Window: {t['start']:%b %d} – {t['end']:%b %d}",
                        f"Change your plants: {PAGES_URL}/lawn-plants.html", claude_line())
            short = t["name"].split(" (")[0]
            if task.get("buy_item") and t["start"] - 7 * DAY >= today:
                out.append((f"{year}-plant-{t['key']}-buy", t["start"] - 7 * DAY, f"{tag}🛒 Buy {task['buy_item']} ({short})", desc))
            if t["start"] >= today:
                out.append((f"{year}-plant-{t['key']}", t["start"], f"{tag}{p['emoji']} {short}: {task['task']}", desc))
            elif year == season:  # window already open: one reminder, pinned to the day we first noticed
                first = state.setdefault("catchup", {}).setdefault(f"plant:{year}:{t['key']}", (today + DAY).isoformat())
                out.append((f"{year}-plant-{t['key']}-now", dt.date.fromisoformat(first),
                            f"⚠️ Now: {p['emoji']} {short}: {task['task']} (by {t['end']:%b} {t['end'].day})", desc))
    return out


def alert_events(state, today):
    out = []
    for key, a in state.get("alerts", {}).items():
        day = dt.date.fromisoformat(a["date"])
        if day >= today:
            out.append((f"alert-{key}", day, a["title"], tail(f"📡 {a['note']}", claude_line())))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="year the tracker statuses belong to")
    ap.add_argument("--today", type=dt.date.fromisoformat)
    args = ap.parse_args()
    cfg = config()
    prog, clim = program(cfg), climate(cfg)
    today = args.today or dt.datetime.now(ZoneInfo(cfg.get("timezone", "America/Chicago"))).date()
    if args.season is None:
        args.season = int(re.search(r"^\*\*Season:\*\* (\d{4})", TRACKER.read_text(encoding="utf-8"), re.M).group(1))

    steps = active_steps(prog, cfg)
    statuses = read_statuses()
    missing = {s["id"] for s in steps} - set(statuses)
    if missing:
        raise SystemExit(f"Status table is missing rows: {sorted(missing)}")
    pending_now = {sid for sid, st in statuses.items() if st.startswith("⬜")}
    pending_next = {s["id"] for s in steps}

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    nxt = args.season + 1
    events = season_events(args.season, today, steps, pending_now, True, state, clim)
    events += season_events(nxt, today, steps, pending_next, False, state, clim)
    events += alert_events(state, today)
    events += plant_events(state, today, (args.season, nxt))
    reset = resolve({"rel": "last_frost", "days": -60}, nxt, clim)
    events.append((f"{nxt}-reset", reset, f"🔄 New lawn season: tap to reset the tracker for {nxt}",
                   tail(f"Starts {nxt} with every step back to ⬜ (your journal is kept).",
                        f"🔄 Reset: {PAGES_URL}/lawn-go.html?reset={nxt}", claude_line())))
    if cfg.get("token_expires"):
        exp = dt.date.fromisoformat(cfg["token_expires"])
        events.append(("lawn-log-token-renew", exp - 14 * DAY, f"🔑 Renew the Lawn Log GitHub token (expires {exp:%b} {exp.day})",
                       f"Create a new token, paste it into the Lawn Log shortcut, and update token_expires in lawn-config.json. "
                       f"Steps: {TRACKER_URL}#lawn-log-shortcut"))
    events.sort(key=lambda e: (e[1], e[0]))

    if state:  # drop catch-up markers for steps that are no longer pending
        live = {f"{args.season}:{sid}" for sid in pending_now}
        live |= {f"plant:{args.season}:{t['key']}" for t in tasks_for(args.season, state) if t["start"] < today <= t["end"]}
        state["catchup"] = {k: v for k, v in sorted(state.get("catchup", {}).items()) if k in live}
        STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Weather-and-Lawn//Lawn Tracker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:🌱 Lawn Care",
        f"X-WR-CALDESC:{esc(prog['name'])}: buy/apply reminders for {esc(cfg.get('label', ''))}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for uid, day, title, desc in events:
        lines += event(uid, day, title, desc, STAMP)
    lines.append("END:VCALENDAR")
    OUT.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", encoding="utf-8")
    print(f"Wrote {len(events)} events to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
