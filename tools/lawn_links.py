"""Shared URLs for the lawn tracker (calendar notes, Next Action box, issue links)."""
from urllib.parse import quote

REPO = "bwspargo3/Weather-and-Lawn"
BRANCH = "claude/lawn-care-tracker"
TRACKER_URL = f"https://github.com/{REPO}/blob/{BRANCH}/LAWN_TRACKER.md"
SESSION_URL = "https://claude.ai/code/session_016pCzefUgtdCddfA8FBqLjL"


def issue_url(sid, action):
    """Pre-filled 'new issue' link that the lawn-tracker-update workflow applies."""
    title = f"✅ {sid} done" if action == "done" else f"❌ {sid} skipped"
    # Notes typed in the issue body go to the journal; a "(Oct 4)" in the title overrides the date.
    return f"https://github.com/{REPO}/issues/new?title={quote(title)}"
