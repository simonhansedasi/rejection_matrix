#!/usr/bin/env python3
"""
Daily job search pipeline — runs via cron, writes new listings to weekly_search_results.
Skips anything already in job_applications or weekly_search_results.
"""

import sys, os, sqlite3, json, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from search import (source_remotive, source_wwr, source_muse, source_usajobs,
                    source_noaa, source_nasa, source_usgs,
                    source_epa, source_usda, source_nps)


def _load_env():
    env_path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '.env'))
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'applications.db')

# Multiple keyword passes — each set is searched independently across all sources.
# Results are deduped by (title, company) within search_jobs before title filtering.
SEARCH_KEYWORD_SETS = [
    ["geospatial"],
    ["remote", "sensing"],
    ["spatial"],
    ["scientist"],
    ["data"],
    ["forward", "deployed"],
    ["applied", "ai", "engineer"],
    ["deployment", "engineer"],
]

TITLE_KEYWORDS = [
    # Geospatial / remote sensing / spatial
    "remote sensing", "geospatial", "spatial analyst", "spatial data",
    "gis analyst", "gis specialist", "gis scientist", "gis developer", "gis engineer",
    "earth observation", "land use analyst", "cartographer",
    # Research / environmental / physical scientist
    "research scientist", "applied scientist", "physical scientist",
    "environmental scientist", "climate scientist", "ecologist",
    "natural resource", "conservation scientist",
    # Data science / engineering
    "data scientist", "data engineer", "analytics engineer", "geospatial engineer",
    # Analyst (qualified — specific prefixes only)
    "research analyst", "data analyst", "environmental analyst",
    "climate analyst", "geospatial analyst", "remote sensing analyst",
    "conservation analyst", "spatial analyst", "policy analyst",
    # Forward Deployed Engineer / applied AI implementation
    "forward deployed", "applied ai engineer", "deployment engineer",
    "implementation engineer", "solutions engineer", "deployment strategist",
]

NEGATIVE_TITLE_KEYWORDS = [
    "senior", "sr.", "lead", "principal", "staff", "director",
    "manager", "head of", "vp ", "vice president", "chief",
]

# Source specs: (fn, non-keyword args tuple, kwargs dict)
# Keywords are prepended per-pass in search_jobs().
SOURCE_SPECS = [
    (source_remotive, (0, 999999, 50),                {}),
    (source_wwr,      (0, 999999),                    {}),
    (source_muse,     (None, 0, 999999, 50),           {}),
    (source_usajobs,  ('Seattle, WA', 0, 999999, 100), {'radius': 100}),
    (source_usajobs,  (None, 0, 999999, 100),          {'remote_only': True, 'source_label': 'USAJOBS-Remote'}),
    # Gov science agencies — prime targets for research/geospatial roles
    (source_noaa,     (None, 0, 999999, 100),          {'remote_only': True}),
    (source_nasa,     (None, 0, 999999, 100),          {'remote_only': True}),
    (source_usgs,     ('Seattle, WA', 0, 999999, 100), {'radius': 200}),
    (source_epa,      ('Seattle, WA', 0, 999999, 100), {'radius': 200}),
    (source_usda,     ('Seattle, WA', 0, 999999, 100), {'radius': 200}),
    (source_nps,      ('Seattle, WA', 0, 999999, 100), {'radius': 200}),
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
    seen = set()
    results = []
    for keywords in SEARCH_KEYWORD_SETS:
        for fn, args, kwargs in SOURCE_SPECS:
            try:
                for j in fn(keywords, *args, **kwargs):
                    key = ((j.get('title') or '').lower().strip(),
                           (j.get('company') or '').lower().strip())
                    if key not in seen:
                        seen.add(key)
                        results.append(j)
            except Exception as e:
                print(f"  source failed ({fn.__name__}, {keywords}): {e}")
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
        and not any(k in (j.get('title') or '').lower() for k in NEGATIVE_TITLE_KEYWORDS)
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
    msg = f"{count} new listings (geospatial/research/scientist/FDE) — check rejection_matrix"
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
