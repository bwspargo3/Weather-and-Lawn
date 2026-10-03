#!/usr/bin/env python3
"""Build lawn-calendar.ics from LAWN_TRACKER.md.

The tracker's Status Table is the source of truth: steps marked ✅ / ❌ / ➖
are dropped from the current season, and only ⬜ steps get calendar events.
The following season is always included in full so reminders keep coming
without anyone touching the file.

Usage:  python3 tools/build_lawn_calendar.py [--season 2026] [--today 2026-10-02]
(defaults: season from the tracker's "**Season:**" line, today in Central time)
"""
import argparse
import datetime as dt
import json
import pathlib
import re
from zoneinfo import ZoneInfo

from lawn_links import SESSION_URL, TRACKER_URL, go_url

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER = ROOT / "LAWN_TRACKER.md"
OUT = ROOT / "lawn-calendar.ics"
STATE = ROOT / "lawn-state.json"  # weather-timed dates from lawn_weather.py (optional)
STAMP = "20260101T000000Z"  # fixed so unchanged events produce an identical file

# id: name, anchor, buy (month, day) + item, window start/end, last-call flag, extra one-off nudges
SCHEDULE = {
    "P":  dict(name="Prep Tasks", anchor="step-prep", buy=(3, 1), item="mower blade sharpening",
               start=(3, 1), end=(3, 15), lastcall=False,
               note="Sharpen blades, rake out debris, check spreader. Confirm spring path with Claude (Idiot-Proof vs Seed Safe)."),
    "1A": dict(name="Step 1: Renovator® (Seed Safe)", anchor="step-1a", buy=(3, 1), item="Renovator®",
               start=(3, 1), end=(3, 20), lastcall=True, note="Apply at bag rate, water in. Seed Safe follows ~3 weeks later."),
    "1B": dict(name="Step 1: PREVENT!®", anchor="step-1b", buy=(3, 8), item="PREVENT!®",
               start=(3, 15), end=(4, 15), lastcall=True,
               note="Apply before soil holds 55°F (check the Lawn tab) / when forsythia blooms. Water in ~½\". No seeding or aerating after."),
    "W1": dict(name="Spring Watering & Mowing Setup", anchor="step-w1", start=(4, 1), end=(4, 15), lastcall=False,
               note="Mow at 3\". Turn irrigation on, check heads, leave it on manual. Water only after 10+ dry days."),
    "2A": dict(name="Step 2: Seed Safe®", anchor="step-2a", buy=(4, 1), item="Seed Safe® + grass seed",
               start=(4, 10), end=(4, 25), lastcall=True, note="Apply and seed. Water lightly 2–3×/day until germination."),
    "2B": dict(name="Step 2: Weed & Feed", anchor="step-2b", buy=(4, 15), item="Weed & Feed",
               start=(4, 22), end=(6, 5), lastcall=True,
               note="Apply to WET grass when dandelions flower. No water or mowing for 24–48 hrs; check for a dry forecast."),
    "S1": dict(name="Grub Preventer", anchor="step-s1", buy=(5, 8), item="grub preventer (chlorantraniliprole, e.g. GrubEx)",
               start=(5, 15), end=(6, 30), lastcall=True, note="Apply and water in ~½\" within 24 hrs."),
    "S2": dict(name="Mosquito Season Start", anchor="step-s2", buy=(5, 8), item="Bti dunks + mosquito barrier spray",
               start=(5, 15), end=(5, 31), lastcall=False,
               note="Dump standing water weekly. Bti dunks in bird baths and barrels. Spray shrubs and shade, not flowers.",
               extra=[((6, 10), "🦟 Mosquito re-spray + fresh Bti dunks"), ((7, 5), "🦟 Mosquito re-spray + fresh Bti dunks"),
                      ((7, 30), "🦟 Mosquito re-spray + fresh Bti dunks"), ((8, 25), "🦟 Mosquito re-spray (last round)")]),
    "3":  dict(name="Step 3: PREVENT!® (2nd app)", anchor="step-3", buy=(5, 15), item="PREVENT!® (2nd bag)",
               start=(5, 22), end=(7, 5), lastcall=True,
               note="Best late May to early June. If you plan to overseed this fall, apply by June 15. Water in ~½\"."),
    "W2": dict(name="Summer Mode: Mowing & Watering", anchor="step-w2", start=(5, 25), end=(8, 31), lastcall=False,
               note="Raise mower to 3.5–4\". Water 1–1.5\"/week in 2–3 deep sessions, 4–9 AM only. No summer fertilizer.",
               extra=[((7, 4), "🔧 Resharpen mower blade")]),
    "S3": dict(name="Brown Patch Watch", anchor="step-s3", start=(6, 15), end=(8, 31), lastcall=False,
               note="Tan circular patches with a smoky edge? Water mornings only. Optional fungicide (azoxystrobin)."),
    "S4": dict(name="Grub Check", anchor="step-s4", start=(8, 15), end=(9, 15), lastcall=False,
               note="Lift 1 sq ft of sod in any brown spot. 10+ grubs → apply Dylox (trichlorfon) and water in."),
    "4":  dict(name="Step 4: Renovator®", anchor="step-4", buy=(8, 25), item="Renovator®",
               start=(9, 1), end=(10, 31), lastcall=True,
               note="Apply at bag rate (3–5 lb/1,000 sq ft if seeding, on seeding day). Water in."),
    "FR": dict(name="Fall Renovation / Overseeding", anchor="step-fr", buy=(8, 20), item="grass seed + aerator/slit-seeder rental",
               start=(9, 1), end=(10, 10), lastcall=True,
               note="Mow low, clear debris, rough up soil, seed, apply Renovator, water lightly 3×/day for 3 weeks.",
               extra=[((8, 25), "🌾 Fall overseed? Decide this week (seed Sep 1–30 is best)")]),
    "W3": dict(name="Fall Watering Shift", anchor="step-w3", start=(9, 1), end=(10, 31), lastcall=False,
               note="Overseeded: light 3×/day for weeks 1–3, then daily, then 1\"/wk. Otherwise ~1\"/wk incl. rain. Mow 3–3.5\"."),
    "S5": dict(name="Fall Broadleaf Weed Control", anchor="step-s5", buy=(9, 28), item="broadleaf weed spray (2,4-D/triclopyr)",
               start=(10, 5), end=(10, 31), lastcall=True,
               note="Spot-spray on a 50–80°F day, no rain for 24 hrs. SKIP if you overseeded."),
    "S6": dict(name="Leaf Management", anchor="step-s6", start=(10, 15), end=(11, 30), lastcall=False,
               note="Mulch-mow light leaves; rake heavy mats, especially off new grass. Repeat weekly while trees drop."),
    "5":  dict(name="Step 5: Snowman®", anchor="step-5", buy=(10, 15), item="Snowman®",
               start=(10, 22), end=(11, 30), lastcall=True,
               note="Apply around the last or second-to-last mow while grass is still green. Water in."),
    "W4": dict(name="Season Close-Out", anchor="step-w4", start=(11, 15), end=(11, 30), lastcall=False,
               note="Final mow ~2.5\". Blow out irrigation before a hard freeze. Stabilize mower fuel and sharpen the blade."),
}
# Steps that don't apply on the chosen spring path (Idiot-Proof). Change if the path changes.
NEXT_SEASON_SKIP = {"1A", "2A"}


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
        f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
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


def season_events(year, today, pending_ids, catch_up, state):
    events = []
    where = state.get("location", {}).get("label", "your area")
    for sid, s in SCHEDULE.items():
        if sid not in pending_ids:
            continue
        d = lambda md: dt.date(year, *md)
        wx = state.get("steps", {}).get(f"{year}:{sid}")
        iso = lambda k: dt.date.fromisoformat(wx[k])
        start = iso("start") if wx else d(s["start"])
        end = iso("end") if wx else d(s["end"])
        buy = (iso("buy") if wx and wx.get("buy") else d(s["buy"])) if s.get("buy") else None
        if end < today:
            continue  # window fully passed
        tag = "📡 " if wx else ""
        link = f"{TRACKER_URL}#{s['anchor']}"
        timing = (f"📡 Weather-timed for {where}: {wx['basis']}\n\n" if wx
                  else "📅 Typical date. It moves automatically once the weather says when.\n\n")
        desc = (f"{timing}{s['note']}\n\nWindow: {start:%b %d} – {end:%b %d}\nDetails: {link}\n\n"
                f"✅ Done: {go_url(sid, 'done')}\n\n❌ Skip: {go_url(sid, 'skip')}\n\n"
                f"💬 Talk to Claude: {SESSION_URL}")
        planned = []
        if buy:
            planned.append(("buy", buy, f"{tag}🛒 Buy {s['item']} ({s['name']})"))
        planned.append(("start", start, f"{tag}🌱 {s['name']}: " + ("apply now" if wx and buy else "window opens")))
        if wx and wx.get("best"):
            planned.append(("best", iso("best"), f"📡 ⭐ Best day: {s['name']}"))
            desc = f"⭐ {wx['best_note']}\n\n" + desc
        if s.get("lastcall") and end - dt.timedelta(days=5) > start:
            planned.append(("last", end - dt.timedelta(days=5), f"{tag}⏰ Last call: {s['name']} (by {end:%b %-d})"))
        if wx and wx.get("repeat_every"):
            k, nxt = 0, start + dt.timedelta(days=wx["repeat_every"])
            while nxt <= dt.date.fromisoformat(wx["repeat_until"]):
                planned.append((f"r{k}", nxt, f"📡 🦟 Mosquito re-spray + fresh Bti dunks"))
                k, nxt = k + 1, nxt + dt.timedelta(days=wx["repeat_every"])
        else:
            for i, (md, title) in enumerate(s.get("extra", [])):
                planned.append((f"x{i}", d(md), title))

        overdue = [p for p in planned if p[1] < today and p[0] in ("buy", "start")]
        for kind, day, title in planned:
            if day < today:
                continue
            events.append((f"{year}-{sid}-{kind}", day, title, desc))
        if catch_up and overdue:
            # First day we noticed it was overdue, so the reminder doesn't slide forward every day.
            key = f"{year}:{sid}"
            first = state.setdefault("catchup", {}).setdefault(key, (today + dt.timedelta(days=1)).isoformat())
            events.append((f"{year}-{sid}-catchup", dt.date.fromisoformat(first),
                           f"⚠️ Now: {s['name']} (window open until {end:%b %d})", desc))
    return events


def alert_events(state, today):
    out = []
    for key, a in state.get("alerts", {}).items():
        day = dt.date.fromisoformat(a["date"])
        if day >= today:
            out.append((f"alert-{key}", day, a["title"], f"📡 {a['note']}\n\n💬 Talk to Claude: {SESSION_URL}"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="year the tracker statuses belong to")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.datetime.now(ZoneInfo("America/Chicago")).date())
    args = ap.parse_args()
    if args.season is None:
        args.season = int(re.search(r"^\*\*Season:\*\* (\d{4})", TRACKER.read_text(encoding="utf-8"), re.M).group(1))

    statuses = read_statuses()
    missing = set(SCHEDULE) - set(statuses)
    if missing:
        raise SystemExit(f"Status table is missing rows: {sorted(missing)}")
    pending_now = {sid for sid, st in statuses.items() if st.startswith("⬜")}
    pending_next = set(SCHEDULE) - NEXT_SEASON_SKIP

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    nxt = args.season + 1
    events = season_events(args.season, args.today, pending_now, True, state)
    events += season_events(nxt, args.today, pending_next, False, state)
    events += alert_events(state, args.today)
    events.append((f"{nxt}-reset", dt.date(nxt, 2, 22), "🔄 Tell Claude: reset the lawn tracker for the new season",
                   f"Ask Claude to reset LAWN_TRACKER.md to ⬜ for {nxt}.\n{TRACKER_URL}"))
    events.append(("lawn-log-token-renew", dt.date(2027, 9, 18), "🔑 Renew the Lawn Log GitHub token (expires ~Oct 2)",
                   "The token inside the Lawn Log shortcut expires about Oct 2, 2027. Create a new one and paste it "
                   f"into the shortcut. Steps: {TRACKER_URL}#lawn-log-shortcut"))
    events.sort(key=lambda e: (e[1], e[0]))

    if state:  # drop catch-up markers for steps that are no longer pending
        live = {f"{args.season}:{sid}" for sid in pending_now}
        state["catchup"] = {k: v for k, v in sorted(state.get("catchup", {}).items()) if k in live}
        STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stamp = STAMP
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Weather-and-Lawn//Lawn Tracker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:🌱 Lawn Care",
        "X-WR-CALDESC:Buy/apply reminders generated from LAWN_TRACKER.md",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for uid, day, title, desc in events:
        lines += event(uid, day, title, desc, stamp)
    lines.append("END:VCALENDAR")
    OUT.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", encoding="utf-8")
    print(f"Wrote {len(events)} events to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
