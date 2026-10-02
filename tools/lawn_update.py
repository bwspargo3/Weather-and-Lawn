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
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

from lawn_links import SESSION_URL, issue_url

TRACKER = pathlib.Path(__file__).resolve().parent.parent / "LAWN_TRACKER.md"
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
        if r["buy"].strip().lower() not in ("n/a", ""):
            lines.append(f"**🛒 Buy by:** {r['buy']}")
        lines += [""] + body + [""]
        lines.append(f"**Done?** [✅ Mark done]({issue_url(r['id'], 'done')}) · "
                     f"[❌ Skip]({issue_url(r['id'], 'skip')}) · [💬 Talk to Claude]({SESSION_URL})")
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
    parts = [l.strip() for l in body.splitlines() if l.strip()]
    return " / ".join(parts)


def apply(text, title, body, created):
    m = TITLE_RE.match(title)
    if not m or not (m.group(1) or m.group(3)):
        raise UpdateError("Title should look like `✅ 4 done` or `❌ S5 skipped` (optionally with a date: `(Oct 4)`).")
    emoji, sid, word, raw_date = m.groups()
    sid = sid.upper()
    skip = emoji == "❌" or (word or "").lower().startswith("skip")
    season = int(re.search(r"^\*\*Season:\*\* (\d{4})", text, re.M).group(1))
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

    notes = clean_notes(body)
    entry = f"- **{fmt_date(when)}** · {new_status.split(' (')[0]} · {sid} · {row['name']}" + (f": {notes}" if notes else "")
    text = text.replace("\n<!-- JOURNAL:END -->", f"\n{entry}\n<!-- JOURNAL:END -->", 1)
    text = render_next_action(text)

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
        TRACKER.write_text(render_next_action(text), encoding="utf-8")
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
