#!/usr/bin/env python3
"""Generate LAWN_TRACKER.md from the chosen program (programs/<id>.json), the location's
climate profile and lawn-config.json, keeping the existing statuses and journal.

  python3 tools/lawn_init.py                 # regenerate (e.g. after changing program/location)
  python3 tools/lawn_init.py --reset 2027    # start a new season: all steps back to ⬜, journal kept
"""
import argparse
import datetime as dt
import re
from zoneinfo import ZoneInfo

import lawn_plants
from lawn_engine import ROOT, active_steps, climate, config, describe, fmt, program, resolve, windows
from lawn_links import BRANCH, PAGES_URL, RAW_URL, REPO, REPO_NAME, SESSION_URL
from lawn_update import render_all, rows

TRACKER = ROOT / "LAWN_TRACKER.md"
HELP = ROOT / "templates" / "tracker-help.md"
NA_PATH = "➖ N/A (other spring path)"


def existing():
    if not TRACKER.exists():
        return {}, "", None, None
    text = TRACKER.read_text(encoding="utf-8")
    statuses = {r["id"]: r["status"] for r in rows(text)}
    m = re.search(r"<!-- JOURNAL:START[^\n]*-->\n(.*?)<!-- JOURNAL:END -->", text, re.S)
    season = re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M)
    size = re.search(r"^\*\*Lawn size:\*\* (.*)$", text, re.M)
    return statuses, m.group(1) if m else "", int(season.group(1)) if season else None, size.group(1) if size else None


def md_date(spec, year, clim):
    d = resolve(spec, year, clim)
    return fmt(d)


def weather_row(s):
    t = s["trigger"]
    extra = []
    if t.get("min") or t.get("max"):
        extra.append("clamped " + " – ".join(x for x in (t.get("min") and f"not before {t['min']}", t.get("max") and f"not after {t['max']}") if x))
    if t.get("best"):
        b = t["best"]
        extra.append(f"⭐ best day = {b.get('lo', 60)}–{b.get('hi', 85)}°F and dry")
    if t.get("heat_skip"):
        extra.append(f"says **skip** if the 5-day average high is ≥ {t['heat_skip']}°F")
    e = t.get("end")
    if e and e.get("type"):
        extra.append(f"last call: {describe(e)}")
    if t.get("repeat"):
        extra.append(f"repeats every {t['repeat']['every']} days")
    return f"| {s['id']} {s['name']} | {describe(t)}" + (". " + "; ".join(extra) if extra else "") + " |"


def generate(reset=None, fresh=False):
    cfg, prog = config(), program()
    clim = climate(cfg)
    statuses, journal, season, size = existing()
    if reset:
        season, statuses = reset, {}
        journal += f"- **— {reset} season started —**\n"
    elif fresh:
        statuses = {}
        journal += f"- **— switched to {prog['name']} —**\n"
    season = season or dt.datetime.now(ZoneInfo(cfg.get("timezone", "America/Chicago"))).year
    path = cfg.get("spring_path")
    active = {s["id"] for s in active_steps(prog, cfg)}
    steps = prog["steps"]

    def status(s):
        st = statuses.get(s["id"])
        if s.get("path") and st and st[0] in "⬜➖":  # spring path changed: swap which path is N/A
            st = None
        if st:
            return st
        return "⬜ Not started" if s["id"] in active else NA_PATH

    def timing(s, long=False):
        if s.get("timing_long" if long else "timing"):
            return s["timing_long" if long else "timing"]
        a, b, _ = windows(s, season, clim)
        return f"{fmt(a)} → {fmt(b)}"

    def buy_text(s):
        if s.get("buy_text"):
            return s["buy_text"]
        _, _, b = windows(s, season, clim)
        return f"~{fmt(b)}" if b else "n/a"

    L = [f"# {prog['title']}", ""]
    L += [*prog.get("intro", [])]
    lf, ff = md_date(clim["last_frost"], season, clim), md_date(clim["first_frost"], season, clim)
    frost_txt = ("frost-free most years (frost-relative dates use a nominal mid-winter)" if clim.get("frost_free")
                 else f"typical last frost **{lf}**, first frost **{ff}**")
    L += [f"**📍 Location:** {cfg.get('label', 'not set')} · USDA zone {clim.get('zone', '?')} · {frost_txt}"
          + ("" if cfg.get("climate") else " _(Kansas City defaults until your climate profile is computed)_"),
          f"**Season:** {season}",
          f"**Lawn size:** {size or '`_____ sq ft`'}"]
    if prog.get("paths"):
        L.append(f"**Spring path:** {prog['paths'].get(path, 'not chosen')}")
    L += ["", "> **How to update:** tap **✅ Done** / **❌ Skip** in any calendar event (or in the box below). The [Lawn Log shortcut](#lawn-log-shortcut) "
          "logs it with no GitHub sign-in, and the tracker and calendar update themselves in about 15 seconds."
          + (" For anything else, tap **💬 Talk to Claude**." if SESSION_URL else ""), "", "---", "",
          "## 🚨 Next Action", "",
          "<!-- NEXT-ACTION:START (auto-generated from the Status Table; edits here are overwritten) -->",
          "> _(generating…)_", "<!-- NEXT-ACTION:END -->", "",
          f"> **📲 iPhone reminders:** subscribe once to the [Lawn Care calendar]({RAW_URL}/lawn-calendar.ics) "
          "(setup steps at the [bottom](#iphone-calendar)). It updates itself whenever this tracker changes.", "", "---", "",
          "## 📅 Status Table", "",
          "| # | Step | Program(s) | Timing | 🛒 Buy by | Status |", "|---|------|------------|--------|-----------|--------|"]
    for s in steps:
        L.append(f"| {s['id']} | [{s.get('table_name', s['name'])}](#{s['anchor']}) | {s.get('programs', '')} | {timing(s)} | {buy_text(s)} | {status(s)} |")
    L += ["", "**Status key:** ⬜ Not started · ✅ Done (date) · ❌ Skipped · ➖ N/A",
          "", "**How the Next Action Box picks a step:** the first row that is still ⬜.", "", "---", "",
          "## 🛒 Shopping Calendar (buy ~1 week before the window opens)", "", "| Buy by | Item | For step |", "|--------|------|----------|"]
    for s in sorted([s for s in steps if s.get("buy_item") and s["id"] in active], key=lambda s: windows(s, season, clim)[2]):
        L.append(f"| {buy_text(s)} | {s['buy_item']} | {s['id']} |")
    L += ["", "---", "", "## 📖 Detailed Timeline", ""]
    for s in steps:
        L += [f'<a id="{s["anchor"]}"></a>', f"### {s.get('heading', s['name'])}",
              f"**Timing:** {timing(s, True)}  **Programs:** {s.get('programs', '')}", f"**Status:** {status(s)}", "",
              s["body"], "", "---", ""]
    L += ['<a id="plants"></a>', "## 🌿 My Plants: Pruning, Feeding & Planting", "",
          f"Reminders for the plants you pick, timed to your frost dates and USDA zone. Tasks marked 📡 move with the weather "
          f"(last frost, soil temperature, hard freeze). Calendar reminders only, with nothing to check off. Pick or change plants on the "
          f"[plant picker]({PAGES_URL}/lawn-plants.html).", "",
          "<!-- PLANTS:START (auto-generated from lawn-config.json + plant-catalog.json) -->", "<!-- PLANTS:END -->", "", "---", "",
          '<a id="weather-timing"></a>', f"## 📡 Weather Timing ({cfg.get('label', 'your location')})", "",
          "Every morning a GitHub Action (`.github/workflows/lawn-weather.yml`) pulls **modeled soil temperature (2½\"), highs/lows, rain "
          "and humidity** for your location from [Open-Meteo](https://open-meteo.com) (3 months back + 16-day forecast) and moves "
          "each step's calendar events to match. No AI involved.", "",
          "| Step | Weather trigger |", "|------|-----------------|"]
    L += [weather_row(s) for s in steps if s.get("trigger") and s["id"] in active]
    L += ["", "**Extra alerts:** 💧 water this week (< 0.75\" rain in 7 days, none coming; seedling advice for 4 weeks after you seed) · "
          "🌧 heavy rain coming · " + ("🍄 brown patch weather · " if prog.get("grass") == "cool" else "") + "🥶 first frost.", "",
          "**How to read the calendar:** 📡 events are weather-timed and say why. Others are typical dates for your frost dates "
          "and move once the weather triggers them. Dates lock once they arrive. Change location or program on the "
          f"[setup page]({PAGES_URL}/lawn-setup.html).", "", "---", ""]
    help_ = HELP.read_text(encoding="utf-8")
    for a, b in (("{PAGES}", PAGES_URL), ("{RAW}", RAW_URL), ("{REPO_NAME}", REPO_NAME), ("{REPO}", REPO), ("{BRANCH}", BRANCH)):
        help_ = help_.replace(a, b)
    L += [help_.rstrip(), "", '<a id="journal"></a>', "## 📝 Journal", "",
          "<!-- JOURNAL:START (newest last) -->" + ("\n" + journal.rstrip("\n") if journal.strip() else ""), "<!-- JOURNAL:END -->", ""]
    if prog.get("sources"):
        L += ["---", "", "*Sources: " + " · ".join(prog["sources"]) + ". Supplemental timing uses standard extension guidance. "
              "Always follow the product label for rates.*", ""]
    text = "\n".join(L)
    today = dt.datetime.now(ZoneInfo(cfg.get("timezone", "America/Chicago"))).date()
    TRACKER.write_text(render_all(text, today), encoding="utf-8")
    return season


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", type=int, metavar="YEAR")
    a = ap.parse_args()
    print(f"Wrote {TRACKER.name} for season {generate(a.reset)}")
