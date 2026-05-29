#!/usr/bin/env python3
"""
Daily job search pipeline — runs via cron, writes new listings to weekly_search_results.
Skips anything already in job_applications or weekly_search_results.
"""

import sys, os, sqlite3, json, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from search import source_remotive, source_wwr, source_muse, source_ramp

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'applications.db')

SEARCH_KEYWORDS = ["integration"]

TITLE_KEYWORDS = [
    "solutions architect", "integration architect", "systems integrator",
    "integration engineer", "integration specialist", "integration manager",
    "business systems", "process automation", "erp integration",
    "business analyst", "systems analyst", "workflow automation",
    "integration", "architect",
]

NTFY_TOPIC = "simonhans-job-alerts"


def setup_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_search_results (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT,
            title    TEXT,
            company  TEXT,
            url      TEXT,
            source   TEXT,
            hidden   INTEGER DEFAULT 0,
            raw_json TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE weekly_search_results ADD COLUMN hidden INTEGER DEFAULT 0")
    except Exception:
        pass  # column already exists
    conn.commit()


def search_jobs():
    results = []
    for fn, args in [
        (source_remotive, (SEARCH_KEYWORDS, 0, 999999, 50)),
        (source_wwr,      (SEARCH_KEYWORDS, 0, 999999)),
        (source_muse,     (SEARCH_KEYWORDS, None, 0, 999999, 50)),
        (source_ramp,     (SEARCH_KEYWORDS, 0, 999999, 100)),
    ]:
        try:
            results.extend(fn(*args))
        except Exception as e:
            print(f"  source failed: {e}")
    print(f"search: {len(results)} raw results")
    return results


def deduplicate(results, conn):
    existing = set()
    for role, company in conn.execute("SELECT role, company FROM job_applications"):
        existing.add(((role or '').lower().strip(), (company or '').lower().strip()))
    for title, company in conn.execute("SELECT title, company FROM weekly_search_results"):
        existing.add(((title or '').lower().strip(), (company or '').lower().strip()))
    fresh = [
        j for j in results
        if ((j.get('title') or '').lower().strip(),
            (j.get('company') or '').lower().strip()) not in existing
    ]
    print(f"dedup: {len(results)} in, {len(fresh)} new")
    return fresh


def filter_results(results):
    filtered = [
        j for j in results
        if any(k in (j.get('title') or '').lower() for k in TITLE_KEYWORDS)
    ]
    print(f"filter: {len(results)} in, {len(filtered)} after title filter")
    return filtered


def write_to_db(results, conn):
    run_date = datetime.utcnow().isoformat()
    conn.executemany(
        "INSERT INTO weekly_search_results (run_date, title, company, url, source, hidden, raw_json) "
        "VALUES (?, ?, ?, ?, ?, 0, ?)",
        [(run_date, j.get('title'), j.get('company'), j.get('url'),
          j.get('source'), json.dumps(j)) for j in results]
    )
    conn.commit()
    print(f"wrote {len(results)} rows to weekly_search_results")
    return len(results)


def notify(count):
    if count == 0:
        print("notify: nothing new, skipping")
        return
    msg = f"{count} new job listings — check rejection_matrix"
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=msg.encode(),
        headers={"Title": "Daily Job Search"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("notify: sent")
    except Exception as e:
        print(f"notify failed (non-fatal): {e}")


def run():
    conn = sqlite3.connect(DB_PATH)
    setup_table(conn)
    results = search_jobs()
    results = deduplicate(results, conn)
    results = filter_results(results)
    count   = write_to_db(results, conn)
    conn.close()
    notify(count)


if __name__ == '__main__':
    run()
