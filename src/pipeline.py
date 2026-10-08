#!/usr/bin/env python3
"""
Daily job search pipeline — runs via cron, writes new listings to weekly_search_results.
Skips anything already in job_applications or weekly_search_results.
"""

import sys, os, sqlite3, json, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from search import source_remotive, source_wwr, source_muse, source_ramp, source_jsearch, source_govjobs
from score import score_pending, score_capability_pending


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
    # AI infrastructure / LLM platform -- added 2026-09-02, primary lane now
    ["llm", "engineer"],
    ["ai", "infrastructure"],
    ["mlops"],
    # Geographer / statistician -- added 2026-09-06, per merascope's siting/
    # regulatory geospatial analysis work being a direct analog to Census-style
    # geographic and statistical roles
    ["geographer"],
    ["statistician"],
    # Commercial geospatial vocabulary -- added 2026-10-06. The non-defense
    # GIS+DS roles (Cotality, Planet Fitness, retail/real-estate/logistics) never
    # say "GIS"; they say site selection / location intelligence / geocoding.
    ["site", "selection"],
    ["location", "intelligence"],
    ["location", "analytics"],
    ["geocoding"],
    ["trade", "area"],
    # Part-time -- added 2026-09-09. Two single-token entries, not ["part","time"]
    # as separate tokens: keywords_match() requires ALL tokens present as substrings
    # ANYWHERE in title+description, so "part" and "time" as separate tokens would
    # false-positive on text like "As part of a growing team..." + "...full-time
    # position" -- neither phrase is part-time, but both tokens would be present.
    ["part-time"],
    ["part time"],
]

TITLE_KEYWORDS = [
    # Geospatial / remote sensing / spatial
    "remote sensing", "geospatial", "spatial analyst", "spatial data",
    "gis analyst", "gis specialist", "gis scientist", "gis developer", "gis engineer",
    "earth observation", "land use analyst", "cartographer",
    # Commercial geospatial -- added 2026-10-06 (see SEARCH_KEYWORD_SETS)
    "location intelligence", "location analytics", "location scientist",
    "location data", "site selection", "geocoding", "trade area",
    "spatial analytics", "spatial scientist", "spatial data scientist",
    # Geographer / statistician -- added 2026-09-06, merascope siting/regulatory
    # analysis work is a direct analog to Census-style geographic and statistical
    # data roles
    "geographer", "statistician", "survey statistician", "mathematical statistician",
    # Research / environmental / physical scientist
    "research scientist", "applied scientist", "physical scientist",
    "environmental scientist", "climate scientist", "ecologist",
    "natural resource", "conservation scientist",
    # Data science / engineering
    "data scientist", "data engineer", "analytics engineer", "geospatial engineer",
    "geoscientist", "data science", "machine learning engineer", "ml engineer",
    "ai engineer", "research engineer", "machine learning scientist",
    # Analyst (qualified — specific prefixes only)
    "research analyst", "data analyst", "environmental analyst",
    "climate analyst", "geospatial analyst", "remote sensing analyst",
    "conservation analyst", "spatial analyst", "policy analyst",
    "quantitative analyst", "business intelligence analyst",
    # Forward Deployed Engineer / applied AI implementation
    "forward deployed", "applied ai engineer", "deployment engineer",
    "implementation engineer", "solutions engineer", "deployment strategist",
    # AI infrastructure / LLM platform (added 2026-09-02 -- primary lane per
    # portfolio/track-record analysis: ai_glue + RippleForge are the rarest,
    # most differentiated evidence, not glaciers)
    "llm engineer", "ai platform engineer", "mlops engineer", "ml platform engineer",
    "ai infrastructure", "llm infrastructure", "ml infrastructure",
    "llm observability", "ai observability", "genai engineer", "generative ai engineer",
]

NEGATIVE_TITLE_KEYWORDS = [
    "senior", "sr.", "sr ", "lead", "principal", "staff", "director",
    "manager", "head of", "vp ", "vice president", "chief",
    # Evergreen / non-postings -- these are perpetual talent-pipeline listings,
    # not a real current opening, and read as stale (seen for real: "General
    # Interest" @ Andromeda, LinkedIn showed it posted 6 months ago) -- added
    # 2026-09-02.
    "general interest", "future opportunities", "talent pool",
    "talent community", "expression of interest", "always hiring",
]

# Sources confirmed unreliable, not just suspected -- blocked outright rather than
# scored. "Remote Zest Jobs" (remotezest.up.railway.app, reached via JSearch):
# listing #64 had "reputed company" as unsubstituted template text 122 times in
# the body (a scraper that never filled in the real employer); listing #69's own
# Apply button redirected to an entirely unrelated "Senior Health Sensing ML Data
# Scientist" posting -- confirmed 2026-09-02, not a one-off.
# "Learn4Good" added 2026-09-02: listing #65 (Innovate!, Inc., "Remote Geospatial
# Data Scientist: ML & Analytics") wanted to charge Simon a fee and required using
# their own resume, not his -- confirmed 2026-09-02.
# "Naukri Mitra" added 2026-09-02: listing #67 -- Simon clicked through, "useless,"
# an Indian job-board aggregator, not a real employer.
# "Recruit.net" and "JobLeads" added 2026-09-29: both keep trying to charge Simon
# for applications.
# "Remote Quest Jobs" and "Remote Drift" added 2026-10-06: same unsubstituted
# "reputed company" template text as Remote Zest Jobs (seen in the live
# 2026-10-06 geospatial search: "Analysis reputed company and Performance",
# "reputed company Imaging US"), plus a UK pound salary on a "remote USA" listing.
SOURCE_BLOCKLIST = {"remote zest jobs", "learn4good", "naukri mitra", "recruit.net", "jobleads",
                    "remote quest jobs", "remote drift"}

# Specific employers confirmed to run a pay-to-apply / mandatory-their-resume
# scheme, blocked even if they surface via a source that's otherwise fine.
COMPANY_BLOCKLIST = {"innovate!, inc."}

# Sources where company == source is EXPECTED and fine -- direct single-company
# boards, not multi-employer aggregators. See the company==source check below.
SELF_MATCH_OK_SOURCES = {"ramp"}

# Source specs: (fn, non-keyword args tuple, kwargs dict)
# Keywords are prepended per-pass in search_jobs().
# Federal sources (USAJOBS, NOAA, NASA, USGS, EPA, USDA, NPS) removed 2026-08-25 --
# off-strategy since federal government was excluded 2026-07-28. The score.py LLM
# pass is the backstop for any federal listing that surfaces via another source.
# source_ramp restored 2026-09-02 -- it silently fell out of this list during the
# 2026-08-25 federal-removal rewrite (never a deliberate drop), which was a real
# contributor to low weekly volume: no API key, no cost, real listings.
# source_census (2026-09-06, reverted same day): added as an explicit exception
# to the federal exclusion, then dropped once the only real current listings
# turned out to be Suitland, MD hybrid -- not commutable from WA, so Census
# sourcing had zero practical value despite the clearance-friction distinction
# being real. Re-add if a genuinely remote Census statistician/geographer req
# ever surfaces -- the org_filter='Census Bureau' wrapper pattern still applies.
SOURCE_SPECS = [
    (source_remotive, (0, 999999, 50),                {}),
    (source_wwr,      (0, 999999),                    {}),
    (source_muse,     (None, 0, 999999, 50),           {}),
    (source_ramp,     (0, 999999, 50),                 {}),
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
    for ddl in [
        "ALTER TABLE weekly_search_results ADD COLUMN hidden INTEGER DEFAULT 0",
        "ALTER TABLE weekly_search_results ADD COLUMN fit_score REAL",
        "ALTER TABLE weekly_search_results ADD COLUMN fit_rationale TEXT",
        "ALTER TABLE weekly_search_results ADD COLUMN strategy_flag TEXT",
        "ALTER TABLE weekly_search_results ADD COLUMN drafted INTEGER DEFAULT 0",
        "ALTER TABLE weekly_search_results ADD COLUMN capability_score REAL",
        "ALTER TABLE weekly_search_results ADD COLUMN capability_rationale TEXT",
    ]:
        try:
            conn.execute(ddl)
        except Exception:
            pass  # column already exists
    conn.commit()


# JSearch aggregates LinkedIn/Indeed/Glassdoor/ZipRecruiter but is metered (RapidAPI
# free tier: 200 req/month) and each call costs num_pages requests. limit=10 forces
# num_pages=1 (one request) per entry below -- each entry is its OWN call, not
# blended into one query string: blending "geospatial" into the AI-infra query
# on 2026-09-02 diluted JSearch's natural-language matching to 0 results, which is
# why it was dropped that day. Re-added 2026-09-25 as its own separate query instead,
# since JSearch/LinkedIn is otherwise the only source SEARCH_KEYWORD_SETS' geospatial/
# data-scientist passes never reach (Remotive/WWR/Muse/Ramp aren't LinkedIn).
# 3 queries/day = ~90-93 req/month, still well under quota with headroom for manual
# `search.py` CLI use.
JSEARCH_DAILY_QUERIES = [
    ["AI infrastructure engineer", "LLM platform"],
    ["data scientist"],
    ["geospatial"],
    # Added 2026-10-06: the commercial lane. Its own call, not blended into the
    # "geospatial" query (blending diluted JSearch to 0 results before, see above).
    # 4 queries/day = ~120 req/month, leaves ~80 for manual search.py use.
    ["location intelligence"],
]


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

    for query in JSEARCH_DAILY_QUERIES:
        try:
            for j in source_jsearch(query, None, 0, 999999, 10, remote_only=True):
                key = ((j.get('title') or '').lower().strip(),
                       (j.get('company') or '').lower().strip())
                if key not in seen:
                    seen.add(key)
                    results.append(j)
        except Exception as e:
            print(f"  source failed (source_jsearch, {query}): {e}")

    # Called once, not looped per SEARCH_KEYWORD_SETS entry like SOURCE_SPECS --
    # it fetches ALL current postings per agency (no server-side keyword filter),
    # so looping it would just re-fetch the same pages N times for nothing.
    # TITLE_KEYWORDS/NEGATIVE_TITLE_KEYWORDS below are what actually filter it.
    try:
        for j in source_govjobs([]):
            key = ((j.get('title') or '').lower().strip(),
                   (j.get('company') or '').lower().strip())
            if key not in seen:
                seen.add(key)
                results.append(j)
    except Exception as e:
        print(f"  source failed (source_govjobs): {e}")

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
        and (j.get('source') or '').lower() not in SOURCE_BLOCKLIST
        and (j.get('company') or '').lower() not in COMPANY_BLOCKLIST
        # company == source (case-insensitive) usually means a multi-employer
        # aggregator never extracted the real employer and stuffed its own name
        # into both fields instead -- confirmed junk twice (Remote Zest Jobs,
        # Naukri Mitra) with this exact signature. Exception: single-company
        # direct sources (currently just "Ramp") legitimately have company ==
        # source == the company's own name -- checked BEFORE testing for this to
        # avoid excluding every real listing from a source like that.
        and ((j.get('source') or '').strip().lower() in SELF_MATCH_OK_SOURCES
             or (j.get('company') or '').strip().lower() != (j.get('source') or '').strip().lower())
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
    conn.row_factory = sqlite3.Row
    setup_table(conn)
    results = search_jobs()
    results = deduplicate(results, conn)
    results = filter_results(results)
    count   = write_to_db(results, conn)
    try:
        score_pending(conn)
    except Exception as e:
        print(f"scoring pass failed (non-fatal): {e}")
    try:
        score_capability_pending(conn)
    except Exception as e:
        print(f"capability scoring pass failed (non-fatal): {e}")
    conn.close()
    notify(count)


if __name__ == '__main__':
    run()
