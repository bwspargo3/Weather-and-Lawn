# Weather-and-Lawn

Two things live here:

- **Ground Conditions** (`index.html`): a phone-friendly dashboard of local weather, soil temperature and lawn/garden signals.
- **🌱 Lawn Care Tracker**: a season tracker that turns a lawn program into iPhone calendar reminders. Steps are **timed by your local weather** (soil temperature, heat, frost) and pruning and planting reminders cover the plants in your yard. No AI is needed to run it.

## What the tracker does

| | |
|---|---|
| 📍 **Any US ZIP code** | Computes your typical last/first frost dates and USDA zone from 15 years of weather history |
| 🌱 **Lawn programs** | GrassPad (Kansas City/Omaha) · generic **cool-season** (fescue, bluegrass, rye) · generic **warm-season** (Bermuda, Zoysia, St. Augustine, Centipede) |
| 📡 **Weather timing** | Every morning it checks soil and air temperature, rain and the 16-day forecast, then moves each step's reminders (🛒 buy a week ahead, ⭐ best day, ⏰ last call) |
| 🌿 **Your plants** | 23 plant types (roses, hydrangeas, fruit trees, vegetables, citrus, palms…) plus custom plants that borrow a type's care schedule |
| 📲 **iPhone calendar** | A subscribed calendar with 9 AM alerts; tap ✅ / ❌ in an event to log it |

Data: [Open-Meteo](https://open-meteo.com) (forecast, soil temperature, 15-year archive) and [Zippopotam.us](https://zippopotam.us) (ZIP → location). Both are free with no API key.

## Set it up for your own yard (about 20 minutes)

You need a free GitHub account and an iPhone.

1. **Copy the repo:** on GitHub, **Fork** (or "Use this template") into your account.
2. **Point it at your copy:**
   - In `lawn-config.json`: set `"repo"` to `your-name/Weather-and-Lawn`, set `"branch"` to `main`, and delete `climate`, `plants`, `claude_url` and `token_expires`.
   - In `lawn-site.json`: set `"branch": "main"`.
   - In `.github/workflows/lawn-weather.yml` and `lawn-tracker-update.yml`: set `TRACKER_BRANCH: main`.
3. **Turn on GitHub Pages:** Settings → Pages → Deploy from branch → `main` / root.
4. **Turn on Actions:** Actions tab → enable workflows. Then run **Lawn weather** once (Actions → Lawn weather → Run workflow).
5. **Build the Lawn Log shortcut** (lets you log updates without signing in to GitHub). Follow *⚡ Lawn Log Shortcut Setup* in `LAWN_TRACKER.md`.
6. **Choose your ZIP and program:** open `https://your-name.github.io/Weather-and-Lawn/lawn-setup.html` on your iPhone → **Save**.
7. **Pick your plants:** `…/lawn-plants.html` → **Save**.
8. **Subscribe to the calendar:** iPhone Settings → Calendar → Accounts → Add Subscribed Calendar → `https://raw.githubusercontent.com/your-name/Weather-and-Lawn/main/lawn-calendar.ics`, then turn **Remove Alerts** off.

## How it works

| File | Role |
|---|---|
| `programs/*.json` | Lawn programs: steps, typical windows (relative to frost dates), weather triggers, instructions |
| `plant-catalog.json` | Plant care profiles: tasks, windows, zone ranges, triggers |
| `lawn-config.json` | Your ZIP, climate profile, program, spring path, plants |
| `tools/lawn_engine.py` | Climate-relative dates and the trigger engine |
| `tools/lawn_weather.py` | Daily weather run → `lawn-state.json` (also `--backtest YEAR`) |
| `tools/lawn_init.py` | Generates `LAWN_TRACKER.md` from the program (keeps statuses and journal) |
| `tools/build_lawn_calendar.py` | Builds `lawn-calendar.ics` |
| `tools/lawn_update.py` | Applies ✅ / ❌ / 🌿 plants / ⚙️ setup / 🔄 new season updates from issues |
| `.github/workflows/` | Daily weather, issue-driven updates, manual backtest |

**Add a program:** copy `programs/cool-generic.json`, edit the steps (date specs like `{"rel": "last_frost", "days": -21}`, triggers like `{"type": "soil_ge", "value": 50, "days": 7}`; see the top of `tools/lawn_engine.py`), and add it to `programs/index.json`.

Always follow product labels for rates. Timings follow university extension guidance; modeled soil temperatures are estimates, not backyard measurements.
