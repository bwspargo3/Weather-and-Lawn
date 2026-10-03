<a id="iphone-calendar"></a>
## 📲 iPhone Calendar Sync

`lawn-calendar.ics` is generated from the Status Table by `tools/build_lawn_calendar.py`. It's rebuilt automatically after every update, whether that comes from a one-tap link or from Claude. Each ⬜ step gets (📡 = weather-timed, see [Weather Timing](#weather-timing)):
- **🛒 Buy** reminder about 1 week before the window opens (or before the weather-predicted apply date)
- **🌱 Window opens** reminder
- **⏰ Last call** reminder 5 days before the window closes

Each one alerts at 9 AM. Its notes link back to its section here and include **✅ Mark done**, **❌ Skip** and **💬 Talk to Claude** links. Steps marked ✅, ❌ or ➖ disappear from the calendar. Next season's events are already included.

**One-time setup (about 1 minute):**
1. iPhone **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar**. On older iOS: Settings → Calendar → Accounts.
2. Server: `{RAW}/lawn-calendar.ics`
3. Tap **Next**, then turn **Remove Alerts OFF**. iOS turns it on by default, and you'd get no notifications.
4. Tap **Save**. Optional: under **Fetch New Data**, set it to *Hourly* so updates show up faster.

**How the one-tap links work:**
- **✅ Done / ❌ Skip** opens a small page ([`lawn-go.html`]({PAGES}/lawn-go.html)). It hands the update to the **Lawn Log** shortcut on your iPhone, which files it as a GitHub issue using a token stored only on your phone.
- A GitHub Action (`.github/workflows/lawn-tracker-update.yml`) applies the update, rebuilds the calendar, replies on the issue and closes it. Only issues opened by the repo owner are processed.
- **Date:** defaults to the day you log it (Central time). If you did it on a different day, start your note with the date, e.g. `(Oct 4) used 3 bags`.
- **💬 Talk to Claude** opens the Claude Code session for free-form changes.
- **No shortcut handy?** The page also has a "Log it on GitHub instead" link (requires GitHub sign-in).

<a id="lawn-log-shortcut"></a>
### ⚡ Lawn Log Shortcut Setup (one time, ~10 min)

**A. Create a GitHub token** (the only time you'll need to sign in to GitHub)
1. In Safari, open **github.com/settings/personal-access-tokens/new**.
2. **Token name:** `Lawn Log`. **Expiration:** the longest available (up to 1 year). Put the expiry date in `lawn-config.json` as `"token_expires": "YYYY-MM-DD"`, and a 🔑 renewal reminder lands in your calendar 2 weeks before.
3. **Repository access:** *Only select repositories* → **{REPO_NAME}**.
4. **Permissions → Repository permissions → Issues:** *Read and write*. Leave everything else as is.
5. **Generate token** and copy it (starts with `github_pat_`). It's only shown once.

**B. Build the shortcut** in the Shortcuts app → **+**
1. Name it exactly **`Lawn Log`** (the links call it by name).
2. Tap the **ⓘ** (Details) and turn on **Show in Share Sheet**. A *Receive* block appears at the top: set it to **Receive Text** input, and set **If there's no input** to **Ask For Text**.
3. Add **Ask for Input** → *Text*. Prompt: `Notes? (optional)`.
4. Add **Get Contents of URL**:
   - URL: `https://api.github.com/repos/{REPO}/issues`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer ` + your token · `Accept` = `application/vnd.github+json`
   - Request Body: **JSON** · `title` (Text) = **Shortcut Input** · `body` (Text) = **Provided Input**
5. Add **Show Notification** → `🌱 Sent: Shortcut Input`. No success check is needed: if the tracker doesn't update, the token is the likely cause.
6. Tap **Done**.

**C. Test it:** tap any ✅ / ❌ link in a lawn calendar event. Safari asks *Open in "Shortcuts"?* → **Open**. The first run asks to connect to api.github.com → **Always Allow**. Leave the note blank (or type `-`), then tap Done. You should see "🌱 Sent", and the tracker updates about 15 seconds later.

---

