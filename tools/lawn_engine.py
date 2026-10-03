"""Shared engine: climate-relative dates, weather data access, and declarative triggers.

Date specs (program steps and plant tasks):
  "03-15"                                  fixed month-day
  {"rel": "last_frost", "days": -21}       relative to the location's median last spring frost
  {"rel": "first_frost", "days": 14}       relative to the median first fall frost

Trigger types (all temperatures °F; soil = modeled 2.5" depth):
  soil_ge / soil_le     N-day average soil temp reaches value         {value, days=5}
  tmax_run_ge           highs ≥ value on N consecutive days           {value, days}
  tmin_run_ge           lows ≥ value on N consecutive days            {value, days}
  tmax_avg_le / _ge     N-day average high ≤ / ≥ value                {value, days=5}
  freeze                first fall day with low ≤ value               {value}
  frost_free            day after the last spring frost (≤32°F), once a frost-free week is in view
  after                 N days after another step (✅ date or its triggered date) {step, days}
Common keys: from / to (search range, date specs), min / max (clamp result), offset (days),
  basis (explanation template: {val} {date} {since} {anchor} {trig} {end}),
  end (how the window closes: a trigger, or {days_after}, with default / default_days / cap / min_days / note),
  best ({lo, hi, dry_hours, note}) picks the first good forecast day, heat_skip (avg high that means "skip").
"""
import datetime as dt
import json
import pathlib

D = dt.date
DAY = dt.timedelta(days=1)
ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- dates & config

def config():
    return json.loads((ROOT / "lawn-config.json").read_text(encoding="utf-8"))


def program(cfg=None):
    cfg = cfg or config()
    return json.loads((ROOT / "programs" / f"{cfg.get('program', 'grasspad')}.json").read_text(encoding="utf-8"))


def climate(cfg=None):
    cfg = cfg or config()
    # Fallback = Kansas City normals, so a missing profile still produces sensible dates.
    return cfg.get("climate") or {"last_frost": "04-10", "first_frost": "10-25", "zone": "6a"}


def resolve(spec, year, clim):
    if spec is None:
        return None
    if isinstance(spec, str):
        m, d = map(int, spec.split("-"))
        return D(year, m, d)
    base = resolve(clim[spec["rel"]], year, clim)
    return base + spec.get("days", 0) * DAY


def fmt(d):
    return f"{d:%b} {d.day}"


def F(x):
    return f"{round(x)}°F"


def active_steps(prog, cfg):
    """Program steps for this config (drops the spring path that wasn't chosen)."""
    path = cfg.get("spring_path")
    return [s for s in prog["steps"] if not s.get("path") or not path or s["path"] == path]


def windows(step, year, clim):
    """Typical (fallback) dates for a step or plant task."""
    start, end = resolve(step["start"], year, clim), resolve(step["end"], year, clim)
    if end < start:  # windows that wrap past Dec 31 (warm climates) keep their order
        end = start + dt.timedelta(days=14)
    buy = resolve(step["buy"], year, clim) if step.get("buy") else (start - 7 * DAY if step.get("buy_item") else None)
    return start, end, buy


# ---------------------------------------------------------------- weather

class Wx:
    def __init__(self, rows):
        self.rows = rows
        self.first, self.last = min(rows), max(rows)

    def avg(self, key, end, n=5):
        vals = [self.rows[end - i * DAY][key] for i in range(n) if end - i * DAY in self.rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if len(vals) == n else None

    def run(self, cond, end, n):
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


def daily_rows(raw):
    """Open-Meteo response → {date: tmax, tmin, rain, soil (mean °F at 6 cm), rh}."""
    hourly = raw["hourly"]
    soil, rh = {}, {}
    for t, s, h in zip(hourly["time"], hourly["soil_temperature_6cm"], hourly["relative_humidity_2m"]):
        if s is not None:
            soil.setdefault(t[:10], []).append(s)
        if h is not None:
            rh.setdefault(t[:10], []).append(h)
    rows, d = {}, raw["daily"]
    for t, hi, lo, p in zip(d["time"], d["temperature_2m_max"], d["temperature_2m_min"], d["precipitation_sum"]):
        if hi is None or lo is None:
            continue
        rows[D.fromisoformat(t)] = dict(tmax=hi, tmin=lo, rain=p or 0.0,
                                        soil=sum(soil[t]) / len(soil[t]) if soil.get(t) else None,
                                        rh=sum(rh[t]) / len(rh[t]) if rh.get(t) else None)
    return rows


# ---------------------------------------------------------------- triggers

def _hit(trig, wx, lo, hi, anchors):
    """Return (day, metric value, run-start day) for the first day in [lo, hi] meeting the trigger."""
    kind, v, n = trig["type"], trig.get("value"), trig.get("days", 5)
    if kind in ("soil_ge", "soil_le", "tmax_avg_le", "tmax_avg_ge"):
        key = "soil" if kind.startswith("soil") else "tmax"
        ge = kind.endswith("_ge")
        d = wx.first_day(lo, hi, lambda d: (lambda a: a is not None and (a >= v if ge else a <= v))(wx.avg(key, d, n)))
        return (d, wx.avg(key, d, n), d - (n - 1) * DAY) if d else None
    if kind in ("tmax_run_ge", "tmin_run_ge"):
        key = kind[:4]
        d = wx.first_day(lo, hi, lambda d: wx.run(lambda r: r[key] >= v, d, n))
        return (d, wx.rows[d][key], d - (n - 1) * DAY) if d else None
    if kind == "freeze":
        d = wx.first_day(lo, hi, lambda d: wx.rows[d]["tmin"] <= v)
        return (d, wx.rows[d]["tmin"], d) if d else None
    if kind == "frost_free":
        frosts = [d for d in wx.rows if lo <= d <= hi and wx.rows[d]["tmin"] <= 32]
        if frosts and wx.last >= max(frosts) + 7 * DAY:
            last = max(frosts)
            return (last + DAY, wx.rows[last]["tmin"], last)
        return None
    if kind == "after":
        a = anchors.get(trig["step"])
        return (a + trig["days"] * DAY, None, a) if a else None
    raise ValueError(f"unknown trigger type {kind}")


def best_day(wx, start, today, lo=60, hi=85, dry_hours=48):
    def ok(d):
        r, nxt = wx.rows.get(d), wx.rows.get(d + DAY)
        return (r and nxt and lo <= r["tmax"] <= hi and r["rain"] < 0.05
                and (nxt["rain"] < 0.10 if dry_hours >= 48 else True))
    return wx.first_day(max(start, today), wx.last - DAY, ok)


def evaluate(item, year, wx, clim, today, anchors):
    """Weather timing for one step/task: {start, end, buy?, basis, best?, best_note?, repeat?} or None."""
    trig = item.get("trigger")
    if not trig:
        return None
    w_start, w_end, w_buy = windows(item, year, clim)
    lo = resolve(trig.get("from"), year, clim) or w_start - 30 * DAY
    hi = resolve(trig.get("to"), year, clim) or w_end
    h = _hit(trig, wx, lo, hi, anchors)
    if not h:
        return None
    trig_day, val, since = h
    start = trig_day + trig.get("offset", 0) * DAY
    if trig.get("min"):
        start = max(start, resolve(trig["min"], year, clim))
    if trig.get("max"):
        start = min(start, resolve(trig["max"], year, clim))

    end, end_note = w_end, ""
    e = trig.get("end")
    if e:
        if "days_after" in e:
            end = start + e["days_after"] * DAY
        elif e.get("trigger_day"):
            end = trig_day
        else:
            eh = _hit(e, wx, start + DAY, resolve(e.get("to"), year, clim) or D(year, 12, 31), anchors)
            if eh:
                end = eh[0]
                end_note = e.get("note", "").format(end=f"{eh[0]:%b} {eh[0].day}")
            elif e.get("default_days"):
                end = start + e["default_days"] * DAY
            elif e.get("default"):
                end = resolve(e["default"], year, clim)
            if e.get("cap"):
                end = min(end, resolve(e["cap"], year, clim))
        end = max(end, start + e.get("min_days", 3) * DAY)
    end = max(end, start + DAY)

    fill = dict(val=F(val) if val is not None else "", date=fmt(trig_day), since=fmt(since),
                anchor=fmt(since), trig=f"{trig_day:%a %b} {trig_day.day}", end=fmt(end))
    basis = trig.get("basis", describe(trig).replace("**", "") + " ({date}).").format(**fill) + end_note
    if trig.get("heat_skip") and wx.covers(start + 4 * DAY) and (wx.avg("tmax", start + 4 * DAY) or 0) >= trig["heat_skip"]:
        basis = f"⚠️ Heat wave forecast (5-day average high {F(wx.avg('tmax', start + 4 * DAY))}): SKIP this one."
    out = dict(start=start, end=end, basis=basis)
    if w_buy:
        out["buy"] = start - 7 * DAY
    if trig.get("best"):
        b = trig["best"]
        d = best_day(wx, start, today, b.get("lo", 60), b.get("hi", 85), b.get("dry_hours", 48))
        if d and d <= end:
            out["best"] = d
            out["best_note"] = b.get("note", "{day}: high {hi}.").format(day=f"{d:%a %b} {d.day}", hi=F(wx.rows[d]["tmax"]))
    if trig.get("repeat"):
        out["repeat_every"] = trig["repeat"]["every"]
        out["repeat_until"] = resolve(trig["repeat"]["until"], year, clim)
        out["repeat_title"] = trig["repeat"].get("title", "Repeat")
    return out


def describe(trig):
    """Plain-English summary of a trigger (for the 📡 Weather Timing table and default basis)."""
    k, v, n = trig["type"], trig.get("value"), trig.get("days", 5)
    return {
        "soil_ge": f"Soil {n}-day average reaches **{v}°F**",
        "soil_le": f"Soil {n}-day average cools to **{v}°F**",
        "tmax_run_ge": f"Highs **{v}°F+** for {n} days",
        "tmin_run_ge": f"Lows **{v}°F+** for {n} days",
        "tmax_avg_le": f"{n}-day average high **≤ {v}°F**",
        "tmax_avg_ge": f"{n}-day average high **≥ {v}°F**",
        "freeze": f"First fall low **≤ {v}°F**" + (f" ({-trig['offset']} days before)" if trig.get("offset", 0) < 0 else ""),
        "frost_free": "After the **last spring frost**" + (f" (+{trig['offset']} days)" if trig.get("offset") else ""),
        "after": f"**{trig.get('days', 0) // 7} weeks after step {trig.get('step')}**",
    }.get(k, k)
