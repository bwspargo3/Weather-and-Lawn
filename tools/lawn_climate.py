#!/usr/bin/env python3
"""Compute a location's climate profile from its ZIP code and save it in lawn-config.json.

  - coordinates and place name: zippopotam.us (free, no key)
  - 15 years of daily lows: Open-Meteo historical archive (ERA5, free, no key)
  - median last spring frost / first fall frost (≤32°F) and USDA hardiness zone
    (average annual extreme minimum, the same definition USDA uses)

Grid weather is smoother than a backyard thermometer, so the zone can read up to half a
zone warm in cold valleys; frost dates are typically within about a week of NOAA normals.

  python3 tools/lawn_climate.py --zip 66220        # set/replace location
  python3 tools/lawn_climate.py                    # (re)compute for the configured ZIP
"""
import argparse
import datetime as dt
import json
import pathlib
import statistics
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "lawn-config.json"
YEARS = 15


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weather-and-lawn"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def geocode(zip_code):
    g = get(f"https://api.zippopotam.us/us/{zip_code}")
    p = g["places"][0]
    return dict(lat=round(float(p["latitude"]), 2), lon=round(float(p["longitude"]), 2),
                label=f"{p['place name']}, {p['state abbreviation']} {zip_code}")


def archive(lat, lon, end_year):
    q = dict(latitude=lat, longitude=lon, start_date=f"{end_year - YEARS + 1}-01-01", end_date=f"{end_year}-12-31",
             daily="temperature_2m_min", temperature_unit="fahrenheit", timezone="auto")
    return get("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(q))


def profile(raw):
    lows = {dt.date.fromisoformat(t): v for t, v in zip(raw["daily"]["time"], raw["daily"]["temperature_2m_min"]) if v is not None}
    years = sorted({d.year for d in lows})
    spring, fall, extremes = [], [], []
    for y in years:
        sp = [d for d in lows if d.year == y and d < dt.date(y, 7, 15) and lows[d] <= 32]
        fa = [d for d in lows if d.year == y and d >= dt.date(y, 7, 15) and lows[d] <= 32]
        spring.append(max(sp).timetuple().tm_yday if sp else None)
        fall.append(min(fa).timetuple().tm_yday if fa else None)
        winter = [v for d, v in lows.items() if dt.date(y - 1, 7, 1) <= d < dt.date(y, 7, 1)]
        if len(winter) > 300:
            extremes.append(min(winter))
    frosty = [s for s in spring if s]
    frost_free = len(frosty) < len(years) / 2
    mmdd = lambda doy: (dt.date(2001, 1, 1) + dt.timedelta(days=round(doy) - 1)).strftime("%m-%d")
    last = mmdd(statistics.median(frosty)) if not frost_free else "01-20"
    first = mmdd(statistics.median([f for f in fall if f])) if not frost_free and any(fall) else "12-20"
    t = statistics.mean(extremes)
    n = int((t + 60) // 10) + 1
    zone = f"{max(1, min(13, n))}{'a' if (t + 60) % 10 < 5 else 'b'}"
    return dict(last_frost=last, first_frost=first, zone=zone, extreme_min=round(t, 1), frost_free=frost_free,
                years=f"{years[0]}–{years[-1]}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip")
    ap.add_argument("--fixture", help="saved archive response (testing)")
    ap.add_argument("--geo", help="saved zippopotam response (testing)")
    args = ap.parse_args(argv)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    zip_code = args.zip or cfg.get("zip")
    if not zip_code:
        raise SystemExit("No ZIP code: pass --zip or set \"zip\" in lawn-config.json")
    if args.geo:
        p = json.loads(pathlib.Path(args.geo).read_text())["places"][0]
        geo = dict(lat=round(float(p["latitude"]), 2), lon=round(float(p["longitude"]), 2),
                   label=f"{p['place name']}, {p['state abbreviation']} {zip_code}")
    else:
        geo = geocode(zip_code)
    raw = json.loads(pathlib.Path(args.fixture).read_text()) if args.fixture else archive(geo["lat"], geo["lon"], dt.date.today().year - 1)
    clim = profile(raw)
    cfg.update(zip=zip_code, **geo, timezone=raw.get("timezone", cfg.get("timezone", "America/Chicago")), climate=clim)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{geo['label']}: zone {clim['zone']}, last frost {clim['last_frost']}, first frost {clim['first_frost']}"
          + (" (frost-free most years)" if clim["frost_free"] else "") + f", from {clim['years']}")


if __name__ == "__main__":
    main()
