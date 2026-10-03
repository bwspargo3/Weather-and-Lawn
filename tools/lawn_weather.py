#!/usr/bin/env python3
"""Weather-time the lawn program from live soil/air data for the location in lawn-config.json.

Pulls ~3 months of history plus a 16-day forecast from Open-Meteo (free, no key),
evaluates each step's weather trigger, and writes lawn-state.json:

  steps["2027:1B"] = {"buy": ..., "start": ..., "end": ..., "best": ..., "basis": "..."}
  alerts[...]      = one-off events (watering, heavy rain, brown patch, frost)

build_lawn_calendar.py uses these dates instead of the fixed typical dates, and
falls back to the typical dates for any step the weather hasn't triggered yet.
Dates that have already arrived are locked so the history doesn't move around.

  python3 tools/lawn_weather.py [--fixture weather.json] [--today 2026-10-03]
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "lawn-config.json"
STATE = ROOT / "lawn-state.json"
TRACKER = ROOT / "LAWN_TRACKER.md"
D = dt.date
DAY = dt.timedelta(days=1)


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


def daily_rows(raw):
    """One row per day: tmax, tmin, rain (in), soil (mean °F at 6 cm ≈ 2.4"), rh (mean %)."""
    hourly = raw["hourly"]
    soil, rh = {}, {}
    for t, s, h in zip(hourly["time"], hourly["soil_temperature_6cm"], hourly["relative_humidity_2m"]):
        day = t[:10]
        if s is not None:
            soil.setdefault(day, []).append(s)
        if h is not None:
            rh.setdefault(day, []).append(h)
    rows = {}
    d = raw["daily"]
    for t, hi, lo, p in zip(d["time"], d["temperature_2m_max"], d["temperature_2m_min"], d["precipitation_sum"]):
        if hi is None or lo is None:
            continue
        rows[D.fromisoformat(t)] = dict(
            tmax=hi, tmin=lo, rain=p or 0.0,
            soil=sum(soil[t]) / len(soil[t]) if soil.get(t) else None,
            rh=sum(rh[t]) / len(rh[t]) if rh.get(t) else None)
    return rows


class Wx:
    def __init__(self, rows):
        self.rows = rows
        self.first, self.last = min(rows), max(rows)

    def avg(self, key, end, n=5):
        vals = [self.rows[end - i * DAY][key] for i in range(n) if end - i * DAY in self.rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if len(vals) == n else None

    def run(self, cond, end, n):
        """cond true on each of the n days ending at `end`."""
        return all(end - i * DAY in self.rows and cond(self.rows[end - i * DAY]) for i in range(n))

    def first_day(self, start, stop, test):
        d = max(start, self.first)
        while d <= min(stop, self.last):
            if test(d):
                return d
            d += DAY
        return None

    def covers(self, day):
        return self.first <= day <= self.last


# ---------------------------------------------------------------- rules

def F(x):
    return f"{round(x)}°F"


def best_day(wx, start, today, lo=60, hi=85, dry_hours=48):
    """First forecast day from max(start, today) that's mild and stays dry for 24–48 hrs."""
    def ok(d):
        r, nxt = wx.rows.get(d), wx.rows.get(d + DAY)
        return (r and nxt and lo <= r["tmax"] <= hi and r["rain"] < 0.05
                and (nxt["rain"] < 0.10 if dry_hours >= 48 else True))
    return wx.first_day(max(start, today), wx.last - DAY, ok)


def rules(wx, year, today, done):
    """Return {sid: step} for one season year. `done` maps sid -> completion date (from the tracker)."""
    out = {}
    y = lambda m, d: D(year, m, d)

    # 1B PREVENT! #1 — crabgrass sprouts once soil holds ~55°F; get the barrier down as it passes 50°F.
    # Not before Mar 15 (GrassPad's window opens mid-March) and on a 7-day average, so a February warm spell can't trigger it early.
    s = wx.first_day(y(3, 15), y(5, 15), lambda d: (wx.avg("soil", d, 7) or 0) >= 50)
    if s:
        e = wx.first_day(s + DAY, y(5, 31), lambda d: (wx.avg("soil", d, 7) or 0) >= 57) or min(s + 21 * DAY, y(4, 30))
        out["1B"] = dict(buy=s - 7 * DAY, start=s, end=max(e, s + 5 * DAY),
                         basis=f"Soil at 2½\" hit a 7-day average of {F(wx.avg('soil', s, 7))} on {s:%b %-d}. "
                               f"Crabgrass sprouts once soil holds ~55°F, so apply now.")
    pre1 = done.get("1B") or (out.get("1B") or {}).get("start")

    # 2B Weed & Feed — weeds actively growing (3 days ≥ 60°F); pick a dry, mild day.
    s = wx.first_day(y(4, 1), y(6, 1), lambda d: wx.run(lambda r: r["tmax"] >= 60, d, 3))
    if s:
        st = dict(buy=s - 7 * DAY, start=s, end=y(6, 5),
                  basis=f"Highs have been 60°F+ for 3 days (since {s - 2 * DAY:%b %-d}), so weeds are actively growing.")
        b = best_day(wx, s, today)
        if b:
            st["best"] = b
            st["best_note"] = (f"{b:%a %b %-d}: high {F(wx.rows[b]['tmax'])}, dry for the next 24–48 hrs. "
                               f"Apply to wet (dewy) grass that morning.")
        out["2B"] = st

    # 3 PREVENT! #2 — ~6 weeks after the first application.
    if pre1:
        s = min(max(pre1 + 42 * DAY, y(5, 15)), y(6, 10))
        out["3"] = dict(buy=s - 7 * DAY, start=s, end=y(7, 5),
                        basis=f"About 6 weeks after your first PREVENT! ({pre1:%b %-d}). If you plan to overseed this fall, apply by Jun 15.")

    # S1 grub preventer — soil warming through 60°F.
    s = wx.first_day(y(4, 20), y(6, 20), lambda d: (wx.avg("soil", d) or 0) >= 60)
    if s:
        out["S1"] = dict(buy=s - 7 * DAY, start=s, end=y(6, 30),
                         basis=f"Soil at 2½\" averaging {F(wx.avg('soil', s))}. Grub preventer goes down before beetles lay eggs.")

    # S2 mosquitoes — nights staying above 50°F.
    s = wx.first_day(y(4, 15), y(6, 30), lambda d: wx.run(lambda r: r["tmin"] >= 50, d, 5))
    if s:
        out["S2"] = dict(buy=s - 7 * DAY, start=s, end=s + 14 * DAY,
                         basis=f"Nights have stayed above 50°F for 5 days (since {s - 4 * DAY:%b %-d}). Mosquito season is starting.",
                         repeat_every=25, repeat_until=y(9, 15))

    # W2 summer mode — first real heat.
    s = wx.first_day(y(5, 1), y(7, 31), lambda d: wx.run(lambda r: r["tmax"] >= 85, d, 3))
    if s:
        out["W2"] = dict(start=s, end=y(8, 31),
                         basis=f"Highs 85°F+ for 3 days (since {s - 2 * DAY:%b %-d}). Raise the mower and switch to summer watering.")

    # FR / 4 / W3 — fall: seed and feed once summer heat breaks; last call as soil cools toward 55°F.
    s = wx.first_day(y(8, 15), y(10, 5), lambda d: (wx.avg("tmax", d) or 99) <= 85)
    if s:
        lc = wx.first_day(s + DAY, y(10, 31), lambda d: (wx.avg("soil", d) or 99) < 55)
        end = min(lc or y(10, 10), y(10, 15))
        out["FR"] = dict(buy=s - 7 * DAY, start=s, end=max(end, s + 7 * DAY),
                         basis=f"Summer heat broke {s:%b %-d} (5-day average high {F(wx.avg('tmax', s))}). Seed germinates best in warm soil and cool air."
                               + (f" Soil is forecast to cool below 55°F around {lc:%b %-d}, so seed before then." if lc else ""))
        s4 = max(s, y(8, 25))
        out["4"] = dict(buy=s4 - 7 * DAY, start=s4, end=y(10, 31),
                        basis=f"Summer heat has broken (since {s:%b %-d}). Feed now so the lawn recovers before winter.")
        out["W3"] = dict(start=s, end=y(10, 31), basis=f"Cooler weather since {s:%b %-d}. Cut back to ~1\"/week unless you seeded.")

    # S5 fall broadleaf — weeds pulling energy to roots; spray on a mild, dry day.
    s = wx.first_day(y(9, 25), y(10, 25), lambda d: (wx.avg("tmax", d) or 99) <= 80)
    if s:
        st = dict(buy=s - 7 * DAY, start=s, end=y(10, 31),
                  basis=f"5-day average high {F(wx.avg('tmax', s))}. Weeds are moving energy to their roots, so spraying works best now.")
        b = best_day(wx, s, today, lo=50, hi=80, dry_hours=24)
        if b:
            st["best"] = b
            st["best_note"] = f"{b:%a %b %-d}: high {F(wx.rows[b]['tmax'])} with no rain. Good spraying day."
        out["S5"] = st

    # 5 Snowman — growth slowing (highs settle under ~55°F), before the ground freezes.
    s = wx.first_day(y(10, 10), y(11, 30), lambda d: (wx.avg("tmax", d) or 99) <= 55)
    if s:
        fr = wx.first_day(s + DAY, y(12, 15), lambda d: wx.rows[d]["tmin"] <= 20)
        out["5"] = dict(buy=s - 7 * DAY, start=s, end=min(fr or y(11, 30), y(12, 15)),
                        basis=f"5-day average high down to {F(wx.avg('tmax', s))}. Growth is slowing, so it's time to winterize."
                              + (f" A hard freeze (≤20°F) is forecast {fr:%b %-d}, so apply before then." if fr else ""))

    # W4 close-out — blow out irrigation before the first hard freeze.
    fz = wx.first_day(y(10, 1), y(12, 31), lambda d: wx.rows[d]["tmin"] <= 28)
    if fz:
        s = max(fz - 2 * DAY, y(10, 1))
        out["W4"] = dict(start=s, end=max(fz, s + DAY),
                         basis=f"First hard freeze (low {F(wx.rows[fz]['tmin'])}) forecast for {fz:%a %b %-d}. Blow out the sprinklers before then.")
    return out


def alerts(wx, today, done):
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

    if D(today.year, 6, 1) <= today <= D(today.year, 9, 15):
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


LABELS = {"1B": "PREVENT!® #1", "2B": "Weed & Feed", "S1": "Grub preventer", "S2": "Mosquito season start",
          "3": "PREVENT!® #2", "W2": "Summer mode (raise mower)", "FR": "Fall seeding window opens",
          "4": "Renovator®", "W3": "Fall watering shift", "S5": "Fall broadleaf spray", "5": "Snowman®",
          "W4": "Blow out irrigation"}


def backtest(cfg, year):
    """Replay a past season's weather through the rules and print when each step would have triggered."""
    today = dt.datetime.now(ZoneInfo(cfg["timezone"])).date()
    end = min(D(year, 12, 31), today - DAY)
    wx = Wx(daily_rows(fetch(cfg, (D(year, 1, 1), end))))
    found = rules(wx, year, D(year, 1, 1), {})
    lines = [f"### {year} replay for {cfg['label']} (weather through {end:%b %-d})", "",
             "| Step | 🛒 Buy | Apply | Window closes | ⭐ First good day | Why |", "|---|---|---|---|---|---|"]
    f = lambda d: f"{d:%a %b %-d}" if d else "–"
    for sid, name in sorted(LABELS.items(), key=lambda kv: found[kv[0]]["start"] if kv[0] in found else D(year, 12, 31)):
        st = found.get(sid)
        if st:
            lines.append(f"| {name} | {f(st.get('buy'))} | **{f(st['start'])}** | {f(st['end'])} | {f(st.get('best'))} | {st['basis']} |")
        else:
            lines.append(f"| {name} | – | not triggered yet | – | – | |")
    frost = wx.first_day(D(year, 9, 1), wx.last, lambda d: wx.rows[d]["tmin"] <= 32)
    lines += ["", f"First fall frost (≤32°F): {f(frost) if frost else 'not yet'}"]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="saved Open-Meteo response to use instead of fetching")
    ap.add_argument("--today", type=D.fromisoformat)
    ap.add_argument("--backtest", type=int, metavar="YEAR", help="print when steps would have triggered in a past year")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    if args.backtest:
        return backtest(cfg, args.backtest)
    today = args.today or dt.datetime.now(ZoneInfo(cfg["timezone"])).date()
    raw = json.loads(pathlib.Path(args.fixture).read_text()) if args.fixture else fetch(cfg)
    wx = Wx(daily_rows(raw))
    text = TRACKER.read_text(encoding="utf-8")
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
    done = completion_dates(text)

    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    steps, prev = {}, old.get("steps", {})
    for year in (season, season + 1):
        fresh = rules(wx, year, today, done if year == season else {})
        for sid, st in fresh.items():
            key = f"{year}:{sid}"
            locked = prev.get(key)
            # Once a step's start date has arrived, keep it fixed (just refresh the best-day pick).
            if locked and D.fromisoformat(locked["start"]) <= today:
                keep = dict(locked)
                if "best" in st and st["best"] >= today:
                    keep["best"], keep["best_note"] = st["best"].isoformat(), st["best_note"]
                steps[key] = keep
            else:
                steps[key] = ser(st)
    for key, st in prev.items():  # keep locked history the 3-month lookback no longer covers
        if key not in steps and D.fromisoformat(st["start"]) <= today:
            steps[key] = st

    al = {k: v for k, v in old.get("alerts", {}).items() if D.fromisoformat(v["date"]) >= today - 30 * DAY}
    for k, v in alerts(wx, today, done).items():
        al.setdefault(k, ser(v))  # first sighting wins, so dates don't drift day to day

    state = dict(location={k: cfg[k] for k in ("label", "lat", "lon")}, steps=dict(sorted(steps.items())),
                 alerts=dict(sorted(al.items())), catchup=old.get("catchup", {}))
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Weather data {wx.first} → {wx.last}; {len(steps)} weather-timed steps, {len(al)} alerts → {STATE.name}")


if __name__ == "__main__":
    main()
