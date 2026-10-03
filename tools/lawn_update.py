#!/usr/bin/env python3
"""Apply a status update to LAWN_TRACKER.md and regenerate its Next Action box.

  python3 tools/lawn_update.py apply --title "✅ 4 done (Oct 4)" [--body "notes"] [--created 2026-10-04T15:00:00Z]
  python3 tools/lawn_update.py render

`apply` updates the Status Table row, the step's Status line in the Detailed
Timeline, appends a Journal entry, and re-renders the Next Action box. It prints
a Markdown summary (used as the GitHub issue reply). Run build_lawn_calendar.py
afterwards to refresh the .ics feed.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

from lawn_links import SESSION_URL, go_url, issue_url
import lawn_plants

TRACKER = pathlib.Path(__file__).resolve().parent.parent / "LAWN_TRACKER.md"
STATE = TRACKER.parent / "lawn-state.json"
TZ = ZoneInfo("America/Chicago")
ROW_RE = re.compile(r"^\| (\w+) \| \[(.+?)\]\(#([\w-]+)\) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \|\s*$")
TITLE_RE = re.compile(r"^\s*(✅|❌)?\s*([0-9A-Za-z]{1,3})\b\s*(done|skipped|skip)?\s*(?:\((.*?)\))?", re.I)
DATE_FORMATS = ["%Y-%m-%d", "%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%b %d", "%B %d", "%m/%d"]


class UpdateError(Exception):
    pass


def rows(text):
    out = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            sid, name, anchor, programs, timing, buy, status = m.groups()
            out.append(dict(id=sid, name=name, anchor=anchor, programs=programs, timing=timing, buy=buy, status=status))
    return out


def parse_date(raw, season):
    raw = raw.replace(",", " ").replace(".", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in DATE_FORMATS:
        try:
            d = dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        return d if "%Y" in fmt or "%y" in fmt else d.replace(year=season)
    raise UpdateError(f"Couldn't read the date `{raw}`. Try a format like `Oct 4` or `2026-10-04`.")


def fmt_date(d):
    return f"{d:%b} {d.day}, {d.year}"


def section_bounds(text, anchor):
    start = text.find(f'<a id="{anchor}"></a>')
    if start < 0:
        raise UpdateError(f"Detailed Timeline section `{anchor}` not found.")
    end = text.find("\n---\n", start)
    return start, (end if end >= 0 else len(text))


def render_next_action(text):
    all_rows = rows(text)
    pending = [r for r in all_rows if r["status"].startswith("⬜")]
    if not pending:
        box = "> ### 🎉 Season complete\n> Every step is done or skipped. Close out the year, then ask Claude to reset the tracker in late February."
    else:
        r = pending[0]
        s, e = section_bounds(text, r["anchor"])
        sec = text[s:e].splitlines()
        timing = next((l for l in sec if l.startswith("**Timing:**")), f"**Timing:** {r['timing']}")
        status_i = next(i for i, l in enumerate(sec) if l.startswith("**Status:**"))
        body = sec[status_i + 1:]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        lines = [f"### ➡️ [{r['name']}](#{r['anchor']})", f"{timing.rstrip()}  **Status:** {r['status']}  "]
        season = re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1)
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        wx = state.get("steps", {}).get(f"{season}:{r['id']}")
        if wx:
            fmt = lambda k: (lambda d: f"{d:%a %b} {d.day}")(dt.date.fromisoformat(wx[k]))
            lines.append("**📡 Weather-timed:** " + (f"🛒 buy by **{fmt('buy')}** · " if wx.get("buy") else "")
                         + f"apply **{fmt('start')} → {fmt('end')}**  ")
            lines.append(f"_{wx['basis']}_" + ("  " if wx.get("best") else ""))
            if wx.get("best"):
                lines.append(f"**⭐ Best day:** {wx['best_note']}")
        elif r["buy"].strip().lower() not in ("n/a", ""):
            lines.append(f"**🛒 Buy by:** {r['buy']} _(typical date; it moves automatically once the weather says when)_")
        lines += [""] + body + [""]
        lines.append(f"**Done?** [✅ Mark done]({go_url(r['id'], 'done')}) · "
                     f"[❌ Skip]({go_url(r['id'], 'skip')})" + (f" · [💬 Talk to Claude]({SESSION_URL})" if SESSION_URL else "") + " "
                     f"<sub>(via GitHub: [✅]({issue_url(r['id'], 'done')}) · [❌]({issue_url(r['id'], 'skip')}))</sub>")
        nxt = [f"[{n['name']}](#{n['anchor']}) ({n['timing']}"
               + (f", 🛒 buy by {n['buy']}" if n["buy"].strip().lower() not in ("n/a", "") else "") + ")"
               for n in pending[1:3]]
        if nxt:
            lines += ["", "**👀 Coming up after this:** " + " → ".join(nxt)]
        box = "\n".join(("> " + l) if l else ">" for l in lines)
    return re.sub(r"(<!-- NEXT-ACTION:START[^\n]*-->\n).*?(\n<!-- NEXT-ACTION:END -->)",
                  lambda m: m.group(1) + box + m.group(2), text, count=1, flags=re.S)


def clean_notes(body):
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    parts = [l.strip() for l in body.splitlines() if l.strip() not in ("", "-", ".")]
    return " / ".join(parts)


def render_all(text, today):
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    return lawn_plants.render_section(render_next_action(text), today, state)


def apply_plants(text, title, today):
    """Title like '🌿 plants: roses, veg, spring-shrub=Grandma's lilac' (or 'none') replaces the plant list."""
    raw = title.split(":", 1)[1] if ":" in title else ""
    entries, bad = lawn_plants.parse_plants(raw)
    if bad:
        raise UpdateError(f"Didn't recognize: {', '.join(bad)}. Use catalog ids ({', '.join(lawn_plants.catalog())}) "
                          "or `profile=My plant name` for a custom plant.")
    lawn_plants.set_plants(entries)
    items = lawn_plants.chosen_items()
    names = ", ".join(f"{i['plant']['emoji']} {i['name']}" for i in items) or "none"
    return render_all(text, today), (f"🌿 Plant list saved: {names}.\n\nPruning, feeding and planting reminders for these "
                                     "are now in your calendar; the next weather run times any weather-triggered ones.")


def apply_setup(title):
    """'⚙️ setup: zip=66220; program=grasspad; path=idiotproof' → config, climate profile, regenerated tracker."""
    import lawn_climate
    import lawn_init
    from lawn_engine import config
    opts = dict(kv.split("=", 1) for kv in re.split(r"\s*;\s*", title.split(":", 1)[-1].strip()) if "=" in kv)
    opts = {k.strip().lower(): v.strip() for k, v in opts.items()}
    index = {p["id"]: p for p in json.loads((TRACKER.parent / "programs" / "index.json").read_text(encoding="utf-8"))}
    cfg = config()
    zip_code, prog = opts.get("zip", cfg.get("zip", "")), opts.get("program", cfg.get("program", "grasspad"))
    if not re.fullmatch(r"\d{5}", zip_code):
        raise UpdateError(f"`{zip_code}` isn't a 5-digit ZIP code.")
    if prog not in index:
        raise UpdateError(f"Unknown program `{prog}`. Choose one of: {', '.join(index)}.")
    path = opts.get("path") or cfg.get("spring_path")
    paths = index[prog].get("paths") or {}
    if paths and path not in paths:
        path = next(iter(paths))
    changed_program = prog != cfg.get("program")
    if zip_code != cfg.get("zip") or not cfg.get("climate"):
        lawn_climate.main(["--zip", zip_code])
        cfg = config()
    cfg.update(program=prog)
    if paths:
        cfg["spring_path"] = path
    else:
        cfg.pop("spring_path", None)
    lawn_plants.CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lawn_init.generate(fresh=changed_program)
    clim = cfg.get("climate", {})
    summary = (f"⚙️ Setup saved: **{cfg.get('label', zip_code)}** · USDA zone {clim.get('zone', '?')} · "
               + ("frost-free most years" if clim.get("frost_free") else f"typical last frost {clim.get('last_frost')}, first frost {clim.get('first_frost')}")
               + f"\n\nProgram: **{index[prog]['name']}**" + (f" · spring path: {paths[path]}" if paths else "")
               + ("\n\nNew program, so the tracker starts fresh (journal kept)." if changed_program else "")
               + "\n\nThe calendar is rebuilt and weather timing refreshes in this run.")
    return TRACKER.read_text(encoding="utf-8"), summary


def apply_reset(title):
    import lawn_init
    m = re.search(r"(\d{4})", title)
    if not m:
        raise UpdateError("Reset needs a year, e.g. `🔄 new season 2027`.")
    lawn_init.generate(reset=int(m.group(1)))
    return TRACKER.read_text(encoding="utf-8"), f"🔄 Season {m.group(1)} started: every step is back to ⬜ and your journal is kept."


def apply(text, title, body, created):
    t = title.lstrip()
    if t.startswith("🌿"):
        return apply_plants(text, title, created)
    if t.startswith("⚙"):
        return apply_setup(title)
    if t.startswith("🔄"):
        return apply_reset(title)
    m = TITLE_RE.match(title)
    if not m or not (m.group(1) or m.group(3)):
        raise UpdateError("Title should look like `✅ 4 done` or `❌ S5 skipped` (optionally with a date: `(Oct 4)`).")
    emoji, sid, word, raw_date = m.groups()
    sid = sid.upper()
    skip = emoji == "❌" or (word or "").lower().startswith("skip")
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
    notes = clean_notes(body)
    lead = re.match(r"^\(([^)]*)\)\s*", notes)
    if not raw_date and lead:  # a note starting "(Oct 4) ..." sets the date (handy from the Shortcut)
        raw_date, notes = lead.group(1), notes[lead.end():]
    when = parse_date(raw_date, season) if raw_date else created

    by_id = {r["id"]: r for r in rows(text)}
    if sid not in by_id:
        raise UpdateError(f"There's no step `{sid}`. Valid IDs: {', '.join(by_id)}.")
    row = by_id[sid]
    new_status = f"{'❌ Skipped' if skip else '✅ Done'} ({fmt_date(when)})"

    text, n = re.subn(rf"^(\| {re.escape(sid)} \|.*\| )[^|]*?( \|\s*)$",
                      lambda mm: mm.group(1) + new_status + mm.group(2), text, count=1, flags=re.M)
    assert n == 1
    s, e = section_bounds(text, row["anchor"])
    sec = re.sub(r"^\*\*Status:\*\* .*$", f"**Status:** {new_status}", text[s:e], count=1, flags=re.M)
    text = text[:s] + sec + text[e:]

    entry = f"- **{fmt_date(when)}** · {new_status.split(' (')[0]} · {sid} · {row['name']}" + (f": {notes}" if notes else "")
    if row["status"] != new_status or notes:  # don't journal an exact repeat (e.g. a double tap)
        text = text.replace("\n<!-- JOURNAL:END -->", f"\n{entry}\n<!-- JOURNAL:END -->", 1)
    text = render_all(text, created)

    summary = [f"Marked **{sid} · {row['name']}** as {new_status}."]
    if row["status"] != "⬜ Not started":
        summary.append(f"(It was previously `{row['status']}`.)")
    if notes:
        summary.append(f"\n📝 Saved to journal: {notes}")
    pending = [r for r in rows(text) if r["status"].startswith("⬜")]
    if pending:
        p = pending[0]
        summary.append(f"\n**🚨 Next up:** {p['name']} ({p['timing']}"
                       + (f", 🛒 buy by {p['buy']}" if p["buy"].strip().lower() not in ("n/a", "") else "") + ").")
    else:
        summary.append("\n🎉 That was the last open step this season.")
    summary.append("\nThe calendar feed has been rebuilt; your iPhone picks it up at its next refresh.")
    return text, "\n".join(summary)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--title", required=True)
    a.add_argument("--body", default="")
    a.add_argument("--created", help="ISO timestamp; its Central-time date is used when the title has no date")
    sub.add_parser("render")
    args = ap.parse_args()

    text = TRACKER.read_text(encoding="utf-8")
    if args.cmd == "render":
        TRACKER.write_text(render_all(text, dt.datetime.now(TZ).date()), encoding="utf-8")
        return
    created = (dt.datetime.fromisoformat(args.created.replace("Z", "+00:00")).astimezone(TZ).date()
               if args.created else dt.datetime.now(TZ).date())
    try:
        text, summary = apply(text, args.title, args.body, created)
    except UpdateError as err:
        print(f"⚠️ {err}")
        sys.exit(1)
    TRACKER.write_text(text, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
