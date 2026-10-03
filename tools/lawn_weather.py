#!/usr/bin/env python3
"""Weather-time the lawn program and plant tasks for the location in lawn-config.json.

Pulls ~3 months of history plus a 16-day forecast from Open-Meteo (free, no key),
evaluates each program step's and plant task's trigger (see lawn_engine.py), and
writes lawn-state.json. build_lawn_calendar.py uses those dates instead of the
typical ones; dates lock once they arrive so history doesn't move.

  python3 tools/lawn_weather.py [--fixture weather.json] [--today 2026-10-03]
  python3 tools/lawn_weather.py --backtest 2026      # replay a past season
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import lawn_plants
from lawn_engine import (D, DAY, F, Wx, active_steps, climate, config, daily_rows, evaluate, fmt, program)

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "lawn-state.json"
TRACKER = ROOT / "LAWN_TRACKER.md"


# ---------------------------------------------------------------- data

def fetch(cfg, span=None):
    """Live: last 92 days + 16-day forecast. span=(start, end): archived model runs for a past range."""
    q = {
        "latitude": cfg["lat"], "longitude": cfg["lon"], "timezone": cfg["timezone"],
        **({"start_date": span[0].isoformat(), "end_date": span[1].isoformat()} if span
           else {"past_days": 92, "forecast_days": 16}),
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "hourly": "soil_temperature_6cm,relative_humidity_2m",
    }
    host = "historical-forecast-api.open-meteo.com" if span else "api.open-meteo.com"
    url = f"https://{host}/v1/forecast?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)



# ---------------------------------------------------------------- rules

def rules(wx, year, today, done, prog, cfg, clim):
    """Weather timing for every program step with a trigger. ✅ dates anchor 'after' steps."""
    anchors, out = dict(done), {}
    for step in active_steps(prog, cfg):
        r = evaluate(step, year, wx, clim, today, anchors)
        if r:
            out[step["id"]] = r
            anchors.setdefault(step["id"], r["start"])
    return out


def plant_rules(wx, year, today, clim):
    out = {}
    for item in lawn_plants.chosen_items():
        for t in item["plant"]["tasks"]:
            r = evaluate(t, year, wx, clim, today, {})
            if r:
                out[f"{item['key']}:{t['id']}"] = dict(start=r["start"], basis=r["basis"])
    return out


def alerts(wx, today, done, grass="cool"):
    """One-off heads-ups from the forecast. Each id is stable, so a re-run doesn't move it."""
    out = {}
    tomorrow = today + DAY
    seeded = done.get("FR")
    seedling = seeded and (today - seeded).days <= 28
    in_season = D(today.year, 4, 1) <= today <= D(today.year, 10, 31)

    past7 = sum(wx.rows[today - i * DAY]["rain"] for i in range(1, 8) if today - i * DAY in wx.rows)
    next3 = sum(wx.rows[today + i * DAY]["rain"] for i in range(0, 3) if today + i * DAY in wx.rows)
    if in_season and past7 < 0.75 and next3 < 0.25:
        wk = tomorrow.isocalendar()
        title = ("💧 New grass: no rain coming. Keep the top ½\" moist" if seedling
                 else f"💧 Water ~1\" this week (only {past7:.1f}\" of rain in 7 days)")
        out[f"water-{wk[0]}w{wk[1]}"] = dict(
            date=tomorrow, title=title,
            note=(f"{past7:.2f}\" of rain in the last 7 days and {next3:.2f}\" forecast in the next 3. "
                  + ("Seedlings: light watering 2–3× a day until they've been mowed twice." if seedling
                     else "Give 1\" total in 1–2 deep sessions, early morning (tuna-can test).")))

    heavy = wx.first_day(today, today + 3 * DAY, lambda d: wx.rows[d]["rain"] >= 0.5)
    if heavy and in_season:
        out[f"rain-{heavy:%Y%m%d}"] = dict(
            date=max(heavy - DAY, tomorrow), title=f"🌧 Heavy rain {heavy:%a} ({wx.rows[heavy]['rain']:.1f}\"): skip watering",
            note="Skip irrigation. Hold off on Weed & Feed or weed spray before the rain. "
                 "Granular PREVENT!/Renovator/Snowman are fine: rain waters them in.")

    if grass == "cool" and D(today.year, 6, 1) <= today <= D(today.year, 9, 15):
        bp = wx.first_day(today, wx.last, lambda d: wx.run(lambda r: r["tmin"] >= 68 and (r["rh"] or 0) >= 80, d, 2))
        if bp:
            out[f"brownpatch-{bp:%Y%m%d}"] = dict(
                date=max(bp - 2 * DAY, tomorrow), title=f"🍄 Brown patch weather ahead ({bp - DAY:%b %-d}–{bp:%-d})",
                note="Warm, humid nights favor brown patch. Water only in the early morning; "
                     "apply fungicide preventively if you've had it before.")

    if D(today.year, 9, 1) <= today <= D(today.year, 11, 30):
        fr = wx.first_day(today, wx.last, lambda d: wx.rows[d]["tmin"] <= 32)
        if fr:
            out[f"frost-{fr.year}"] = dict(
                date=max(fr - DAY, tomorrow), title=f"🥶 First frost forecast {fr:%a %b %-d} (low {F(wx.rows[fr]['tmin'])})",
                note="Don't mow or walk on frosty grass. Cover tender plants. Seedlings survive light frost fine.")
    return out



# ---------------------------------------------------------------- state

def completion_dates(text):
    """sid -> date for steps marked ✅ Done (skips don't anchor follow-on timing)."""
    done = {}
    for m in re.finditer(r"^\| (\w+) \|.*\| ✅ Done \((\w{3} \d{1,2}, \d{4})\) \|\s*$", text, re.M):
        done[m.group(1)] = dt.datetime.strptime(m.group(2), "%b %d, %Y").date()
    return done


def ser(step):
    return {k: (v.isoformat() if isinstance(v, D) else v) for k, v in step.items()}


def merge(prev, fresh, today):
    """Fresh dates win until a date arrives; then it's locked (history survives the 3-month lookback)."""
    out = {}
    for key, st in fresh.items():
        locked = prev.get(key)
        if locked and D.fromisoformat(locked["start"]) <= today:
            keep = dict(locked)
            if "best" in st and st["best"] >= today:
                keep["best"], keep["best_note"] = st["best"].isoformat(), st["best_note"]
            out[key] = keep
        else:
            out[key] = ser(st)
    for key, st in prev.items():
        if key not in out and D.fromisoformat(st["start"]) <= today:
            out[key] = st
    return dict(sorted(out.items()))


def backtest(cfg, year):
    """Replay a past season's weather through the program and print when each step would have triggered."""
    prog, clim = program(cfg), climate(cfg)
    today = dt.datetime.now(ZoneInfo(cfg["timezone"])).date()
    end = min(D(year, 12, 31), today - DAY)
    wx = Wx(daily_rows(fetch(cfg, (D(year, 1, 1), end))))
    found = rules(wx, year, D(year, 1, 1), {}, prog, cfg, clim)
    lines = [f"### {year} replay: {prog['name']} for {cfg['label']} (weather through {fmt(end)})", "",
             "| Step | 🛒 Buy | Apply | Window closes | ⭐ First good day | Why |", "|---|---|---|---|---|---|"]
    f = lambda d: f"{d:%a %b} {d.day}" if d else "–"
    trig_steps = [s for s in active_steps(prog, cfg) if s.get("trigger")]
    for s in sorted(trig_steps, key=lambda s: found[s["id"]]["start"] if s["id"] in found else D(year, 12, 31)):
        st = found.get(s["id"])
        if st:
            lines.append(f"| {s['name']} | {f(st.get('buy'))} | **{f(st['start'])}** | {f(st['end'])} | {f(st.get('best'))} | {st['basis']} |")
        else:
            lines.append(f"| {s['name']} | – | not triggered | – | – | |")
    frost = wx.first_day(D(year, 9, 1), wx.last, lambda d: wx.rows[d]["tmin"] <= 32)
    lines += ["", f"First fall frost (≤32°F): {f(frost) if frost else 'not yet'}"]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="saved Open-Meteo response to use instead of fetching")
    ap.add_argument("--today", type=D.fromisoformat)
    ap.add_argument("--backtest", type=int, metavar="YEAR", help="print when steps would have triggered in a past year")
    ap.add_argument("--program", help="with --backtest: replay a different program (e.g. cool-generic)")
    args = ap.parse_args()

    cfg = config()
    if args.backtest:
        if args.program:
            cfg = dict(cfg, program=args.program)
            if args.program != "grasspad":
                cfg.pop("spring_path", None)
        return backtest(cfg, args.backtest)
    prog, clim = program(cfg), climate(cfg)
    today = args.today or dt.datetime.now(ZoneInfo(cfg["timezone"])).date()
    raw = json.loads(pathlib.Path(args.fixture).read_text()) if args.fixture else fetch(cfg)
    wx = Wx(daily_rows(raw))
    text = TRACKER.read_text(encoding="utf-8")
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
    done = completion_dates(text)

    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    fresh_steps, fresh_plants = {}, {}
    for year in (season, season + 1):
        for sid, st in rules(wx, year, today, done if year == season else {}, prog, cfg, clim).items():
            fresh_steps[f"{year}:{sid}"] = st
        for key, st in plant_rules(wx, year, today, clim).items():
            fresh_plants[f"{year}:{key}"] = st
    steps = merge(old.get("steps", {}), fresh_steps, today)
    plants = merge(old.get("plants", {}), fresh_plants, today)

    al = {k: v for k, v in old.get("alerts", {}).items() if D.fromisoformat(v["date"]) >= today - 30 * DAY}
    for k, v in alerts(wx, today, done, prog.get("grass", "cool")).items():
        al.setdefault(k, ser(v))  # first sighting wins, so dates don't drift day to day

    state = dict(location={k: cfg[k] for k in ("label", "lat", "lon")}, steps=steps, plants=plants,
                 alerts=dict(sorted(al.items())), catchup=old.get("catchup", {}))
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Weather data {wx.first} → {wx.last}; {len(steps)} weather-timed steps, {len(plants)} plant tasks, {len(al)} alerts → {STATE.name}")


if __name__ == "__main__":
    main()
