# rejection_matrix

Two CLI tools + a Flask web UI + a daily pipeline that surfaces new listings automatically.

- **`src/search.py`** — search job boards for listings by title, salary, location, work type, industry, and company
- **`src/rm.py`** — track applications you've submitted, log updates, and view status
- **`src/app.py`** — web UI on port 5004; search page streams results live, applications page shows the full tracker, Uncovered tab shows pipeline-surfaced jobs
- **`src/pipeline.py`** — daily cron script; searches broad keyword across sources (including FDE terms: forward deployed, applied AI engineer, deployment/implementation/solutions engineer, added 2026-07-14), dedupes, title-filters, writes survivors to `weekly_search_results`, notifies via ntfy.sh

The CLI tools have no dependencies beyond Python 3.6+ stdlib. The web UI requires `flask`. The SQLite database lives at `data/applications.db`.

## Working on the go

To work on job search at a library or coffee shop without the laptop lid suspending your session:

```bash
~/coding/incognito_mode.sh   # run once = lid-close does nothing; run again = back to normal
```

Requires `sudo`. Script lives outside this directory at `~/coding/incognito_mode.sh`.

## Web UI

Run locally:

```bash
python3 src/app.py
# → http://localhost:5004
```

On the Pi it runs as a systemd service (`rejection-matrix.service`). To redeploy:

```bash
rsync -av --exclude='__pycache__' --exclude='*.pyc' --exclude='venv' \
  /home/simonhans/coding/rejection_matrix/ simonhans@raspberrypi:~/coding/rejection_matrix/
ssh simonhans@raspberrypi "sudo systemctl restart rejection-matrix"
```

## CLI

Run both from the repo root:

```bash
python3 src/search.py <keywords> [options]
python3 src/rm.py <command> [options]
```

---

## search.py — find jobs

```
python3 src/search.py <keywords...> [options]
```

### Job boards searched

| Board | Requires key? | What it has |
|---|---|---|
| **Remotive** | No | Remote jobs; salary data on many listings; keyword + category filters |
| **We Work Remotely** | No | Remote jobs; no salary; RSS feed |
| **The Muse** | No | Remote, hybrid, and onsite; location filter; no salary |
| **JSearch** | Yes — free | Aggregates **LinkedIn, Indeed, Glassdoor, ZipRecruiter**; salary data; location; remote filter |
| **USAJOBS** | Yes — free | US federal jobs: **NPS, USGS, NOAA, Forest Service**, and all other federal agencies; salary data; location + radius |

Every result shows a **Source** column (which board — or for JSearch, which underlying site like LinkedIn or Indeed) and a **URL** column (direct link to the listing). That URL is where you apply. When you save a result to the tracker with `--save`, the board name is stored in the `source` field and the listing URL is stored in `notes`.

#### Setting up JSearch (LinkedIn / Indeed / Glassdoor)

JSearch is a RapidAPI service that scrapes the major boards so you don't have to.

1. Sign up free at **rapidapi.com** → search for **JSearch** → subscribe to the free plan (200 req/month)
2. Copy your RapidAPI key and set it in your environment:

```bash
export JSEARCH_API_KEY=your_key_here
# Add to ~/.bashrc or ~/.zshrc to persist it
```

Once the env var is set, JSearch runs automatically alongside the other three sources. Without it, it prints `SKIPPED` and the other sources still run.

#### Setting up USAJOBS (federal jobs)

USAJOBS is the official US federal job board. The API is free but requires registration.

1. Register at **https://developer.usajobs.gov/APIRequest/Index** — fill in your name, email, and intended use
2. You'll receive an API key by email
3. Set both required env vars:

```bash
export USAJOBS_API_KEY=your_key_here
export USAJOBS_EMAIL=your_email@example.com   # must match registration email
# Add to ~/.bashrc or ~/.zshrc to persist
```

Once both vars are set, USAJOBS runs automatically. Without them, it prints `SKIPPED`.

### Options

| Flag | Description |
|---|---|
| `keywords` | Job title keywords. Quote multi-word phrases: `"data analyst"` |
| `--zip ZIP` | US zip code — resolves to city/state for location filtering |
| `--radius MILES` | Search radius around zip, default 25 (applies to onsite/hybrid on The Muse) |
| `--salary-min N` | Hide listings where the *known* max salary is below N |
| `--salary-max N` | Hide listings where the *known* min salary is above N |
| `--remote` | Show only remote listings |
| `--hybrid` | Show only hybrid listings |
| `--industry TEXT` | Category/industry passed to Remotive and The Muse APIs (e.g. `fintech`, `data`, `marketing`) |
| `--company NAME` | Filter results to companies whose name contains this string |
| `--sources LIST` | Comma-separated list of boards to search (default: `remotive,wwr,muse,jsearch,usajobs`) |
| `--limit N` | Max results to fetch per source, default 25 |
| `--save` | After displaying results, prompt for numbers to save to the tracker |

> **Note on salary filtering:** Most listings don't include salary. The filter only excludes listings where salary is *known* to be outside your range — listings with no salary posted will still appear.

### Examples

```bash
# Basic search
python3 src/search.py "data analyst"

# Remote only, with salary floor
python3 src/search.py "data analyst" --remote --salary-min 90000

# Local + remote, near a zip code
python3 src/search.py "software engineer" --zip 98101 --salary-min 120000

# Remote or hybrid, specific industry
python3 src/search.py "product manager" --remote --hybrid --industry fintech

# Search for roles at a specific company
python3 src/search.py "engineer" --company Stripe --remote

# Narrow to two sources, higher result cap
python3 src/search.py "data engineer" --sources remotive,wwr --limit 50

# Search and save picks to the tracker
python3 src/search.py "analyst" --remote --salary-min 80000 --save

# Federal jobs only (NPS, USGS, NOAA, etc.)
python3 src/search.py "park ranger" --zip 98087 --sources usajobs
python3 src/search.py "geologist" --zip 98087 --sources usajobs
python3 src/search.py "physical scientist" --zip 98087 --sources usajobs --remote
```

### Saving to the tracker

Pass `--save` and after results are shown you'll be prompted:

```
Enter result numbers to save to tracker (e.g. 1,3,5), or press Enter to skip:
  > 2,5
  Saved #7: Senior Data Analyst @ Acme Corp
  Saved #8: Data Analyst @ Some Co
```

Saved entries land in `data/applications.db` with status `applied`. The listing URL is in the `notes` field — retrieve it with:

```bash
sqlite3 data/applications.db "SELECT notes FROM job_applications WHERE id = 7;"
```

---

## rm.py — track applications

### `add` — log a new application

```
python3 src/rm.py add <company> <role> [options]
```

| Argument | Required | Description |
|---|---|---|
| `company` | yes | Company name |
| `role` | yes | Job title |
| `--date YYYY-MM-DD` | no | Date applied (defaults to today) |
| `--source` | no | Where you found it (LinkedIn, referral, etc.) |
| `--location` | no | Office location or "Remote" |
| `--salary` | no | Salary offer (number) |
| `--industry` | no | Company industry (e.g. Fintech, Healthcare) |
| `--notes` | no | Any free-text notes |
| `--variant` | no | Resume variant used (e.g. `geo`, `research`, `simulation`) |

New applications are always set to `applied` status.

```bash
python3 src/rm.py add "PNNL" "Research Scientist" --source LinkedIn --location Remote --variant research
python3 src/rm.py add "Boring Co" "Engineer" --date 2026-03-20 --notes "Applied via recruiter"
```

---

### `update` — log what happened

```
python3 src/rm.py update <id> <note> [--status STATUS] [--variant VARIANT]
```

Every update is written to the audit log with a timestamp. `--status` changes the application status; `--variant` updates which resume was used. Both are recorded with old/new values.

```bash
# Log a note without changing status
python3 src/rm.py update 3 "Had a phone screen, seemed positive"

# Log a note and change status
python3 src/rm.py update 3 "Got the rejection email" --status rejected
python3 src/rm.py update 7 "No reply in 3 weeks" --status ghosted
python3 src/rm.py update 12 "Turned out to be a recruiter farm" --status scam

# Update the resume variant (e.g. if you forgot to set it on add)
python3 src/rm.py update 5 "switched to geo resume" --variant geo
```

**Valid statuses:**

| Status | Meaning |
|---|---|
| `applied` | Submitted, waiting |
| `interviewing` | Active process |
| `offer` | Offer received |
| `rejected` | Formal rejection |
| `ghosted` | No response |
| `dead` | Role pulled / company went quiet |
| `scam` | Fake listing or bad-faith recruiter |

---

### `list` — view applications

```
python3 src/rm.py list [--status STATUS]
```

Shows all applications sorted by date, newest first. Statuses are colour-coded. Salary and resume variant are shown where set; industry is stored but not displayed (query directly via SQLite). Pass `--status` to filter.

```bash
python3 src/rm.py list
python3 src/rm.py list --status applied
python3 src/rm.py list --status interviewing
```

---

### `docs` — manage documents attached to an application

```
python3 src/rm.py docs add <app_id> <path> [--type TYPE] [--label LABEL]
python3 src/rm.py docs list <app_id>
python3 src/rm.py docs rm <doc_id>
```

Attach resumes, cover letters, portfolios, or any other files to an application record. Multiple documents per application are supported.

| Subcommand | Description |
|---|---|
| `docs add <app_id> <path>` | Attach a file path to an application |
| `docs list <app_id>` | List all documents for an application |
| `docs rm <doc_id>` | Remove a document entry (does not delete the file) |

`--type` accepts: `resume`, `cover_letter`, `portfolio`, `writing_sample`, `other` (default: `other`)

`--label` is an optional free-text tag, useful for versioning or context (e.g. `"resume_v2"`, `"tailored fintech"`).

```bash
# Attach a tailored resume and a cover letter
python3 src/rm.py docs add 5 ~/resumes/resume_fintech_v2.pdf --type resume --label "tailored fintech"
python3 src/rm.py docs add 5 ~/cover_letters/acme_cover.pdf --type cover_letter

# List all docs for application #5
python3 src/rm.py docs list 5

# Remove a document entry
python3 src/rm.py docs rm 3
```

---

### `activity` — job search time from personal tracker

```
python3 src/rm.py activity [--days N]
```

Reads time blocks categorised as **Job Search** or **LinkedIn** from the personal tracker (`../dada_science/personal_tracker/personal.db`) and shows them grouped by day with totals. Defaults to the last 14 days.

```bash
python3 src/rm.py activity
python3 src/rm.py activity --days 30
```

Requires the personal tracker app to have been run at least once so its database exists.

---

## Database

Two tables in `data/applications.db`:

- **`job_applications`** — one row per application
- **`application_log`** — append-only audit trail; every `update` call writes here
- **`application_documents`** — file paths attached to applications (resume, cover letter, etc.)

To inspect directly:

```bash
sqlite3 data/applications.db "SELECT * FROM job_applications ORDER BY date_applied DESC;"
sqlite3 data/applications.db "SELECT * FROM application_log WHERE application_id = 3;"
```
