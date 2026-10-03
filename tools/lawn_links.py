"""Shared URLs for the lawn tracker, built from lawn-config.json (repo, branch, optional claude_url)."""
import json
import pathlib
from urllib.parse import quote

_cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "lawn-config.json").read_text(encoding="utf-8"))
REPO = _cfg.get("repo", "OWNER/REPO")
BRANCH = _cfg.get("branch", "main")
OWNER, REPO_NAME = REPO.split("/", 1)
TRACKER_URL = f"https://github.com/{REPO}/blob/{BRANCH}/LAWN_TRACKER.md"
PAGES_URL = f"https://{OWNER.lower()}.github.io/{REPO_NAME}"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
SESSION_URL = _cfg.get("claude_url")  # optional "💬 Talk to Claude" link; omitted when unset


def issue_url(sid, action):
    """Pre-filled 'new issue' link that the lawn-tracker-update workflow applies."""
    title = f"✅ {sid} done" if action == "done" else f"❌ {sid} skipped"
    # Notes typed in the issue body go to the journal; a "(Oct 4)" in the title overrides the date.
    return f"https://github.com/{REPO}/issues/new?title={quote(title)}"


def go_url(sid, action):
    """Quick-log link: lawn-go.html (GitHub Pages) hands the update to the 'Lawn Log' iOS Shortcut."""
    return f"{PAGES_URL}/lawn-go.html?s={sid}&a={'skip' if action == 'skip' else 'done'}"


def claude_line(prefix="💬 Talk to Claude: "):
    return f"{prefix}{SESSION_URL}" if SESSION_URL else ""
