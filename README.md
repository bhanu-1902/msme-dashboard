# The Ledger — India MSME & Trade Intelligence

A self-updating dashboard tracking India's MSME registrations, merchandise trade,
and industrial production, with **year-on-year and month-on-month change computed
from official government releases**.

No server to run. No hosting cost. A scheduled GitHub Action fetches the data and
commits it; GitHub Pages serves the site.

---

## Why it's built this way

Government portals cannot be fetched from a browser. DGFT and TRADESTAT actively
block automated requests, and virtually none of these sites send CORS headers, so
client-side `fetch()` fails regardless. Any dashboard claiming to pull live
government data directly in the browser either isn't doing that, or is silently
broken.

So the fetching happens **server-side on a schedule**:

```
GitHub Action (cron)  →  scripts/fetch_data.py  →  data/latest.json  →  GitHub Pages
```

The page reads a committed JSON file. It loads instantly, works when source
portals are down, and every number carries its own source URL and as-of date.

---

## Setup (about 10 minutes)

### 1. Create the repository

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/msme-dashboard.git
git push -u origin main
```

### 2. Get a data.gov.in API key (free, optional but recommended)

Register at <https://data.gov.in>. Without a key the MSME series is skipped;
trade and IIP still work.

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `DATAGOVIN_API_KEY`
- Value: your key

### 3. Enable GitHub Pages

**Settings → Pages → Source: Deploy from a branch → Branch: `main` / root**

Your site goes live at:
`https://YOUR_USERNAME.github.io/msme-dashboard/site/`

### 4. Allow the Action to commit

**Settings → Actions → General → Workflow permissions → Read and write permissions**

### 5. Trigger the first run

**Actions → Refresh dashboard data → Run workflow**

---

## Local development

Browsers block `fetch()` on `file://` URLs, so open it through a local server:

```bash
python3 -m http.server 8000
# then visit http://localhost:8000/site/
```

To regenerate data locally:

```bash
pip install requests
export DATAGOVIN_API_KEY="your-key"
python scripts/fetch_data.py
```

---

## How it updates

| Source | Series | Release timing | Cron |
|---|---|---|---|
| Ministry of Commerce | Merchandise trade | ~15th monthly | 16th, 06:00 UTC |
| MOSPI / NSO | IIP (base 2022-23) | ~28th monthly | 29th, 06:00 UTC |
| data.gov.in API | Udyam MSME registrations | Rolling | Both runs |

You can also trigger a refresh manually from the Actions tab at any time.

---

## Design decisions worth knowing

**Never fabricate a number.** If a source fails, the previous value is retained and
flagged `stale: true`. The UI shows a `STALE` marker and a warning banner. An old
number labelled old is far safer than a fresh-looking guess — especially if you're
putting this in front of stakeholders.

**Percentage points vs percent.** IIP is *already published as* a year-on-year
growth rate. Computing percent-change on it (7.8% vs 3.6% → "+116%") is
arithmetically valid but meaningless. Rate fields listed in `RATE_FIELDS` are
compared in percentage **points** and render as `+4.2 pp`. Level metrics — trade
values, registration counts — use percent as normal.

**History is append-only.** `data/history.json` accumulates every observation.
YoY compares against the actual observation from twelve months ago, not an
assumption. The longer it runs, the more useful it becomes.

**Deficit direction is inverted.** A rising trade deficit renders red, not green.

---

## Scrapers are brittle — this is expected

`fetch_trade()` and `fetch_iip()` parse HTML. When those pages are redesigned, the
regexes will break. That's normal and the design accounts for it: the run keeps the
last good value, marks it stale, and the site keeps working while you fix the
pattern.

To repair one, open the source URL, find how the figure is now worded, and update
the regex in the corresponding function. The `_parse_billions()` helper accepts
multiple patterns and returns the first that matches, so you can add a new pattern
without removing the old one.

Adding a source: write a `fetch_yourthing()` returning a dict (or `None` on
failure), then follow the existing pattern in `main()` — append to history, build a
series block, add a fallback branch.

---

## Repository layout

```
.github/workflows/update-data.yml   Scheduled refresh
scripts/fetch_data.py               Fetch + YoY/MoM computation
scripts/seed_latest.py              One-time initial data generation
data/history.json                   Append-only observation history
data/latest.json                    What the site reads
site/index.html                     The dashboard (no dependencies)
```

---

## Caveats

All figures are provisional and subject to official revision — the Commerce
Ministry routinely revises prior months. Services trade data lags merchandise by
roughly a month. This dashboard is a monitoring aid, not a substitute for reading
the primary releases before making a decision that matters.
