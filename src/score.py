#!/usr/bin/env python3
"""
Score pending weekly_search_results rows for fit against Simon's job-search
strategy, using a small LLM call per row. Federal/clearance-gated listings
and named dealbreakers get hidden=1 automatically (same semantics as the
existing manual Hide button); everything else gets a fit_score + rationale
so the Uncovered page can rank by it. A merely low score is NOT hidden --
only pre-established hard rules are.

Usage:
    python3 score.py [--limit N]
    python3 score.py --dry-run
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))

STRATEGY = """\
Primary target (revised 2026-09-10, after a strategy/capability alignment review): \
a generalist analyst/engineer who independently designs, builds, and operates real \
production software (Merascope, RippleForge, ai_glue -- solo-shipped, live, real ops) \
AND applies rigorous quantitative/research method to whatever domain is in front of \
him (published glacier-thickness research, applied geospatial/climate data fusion \
pipelines). The differentiator is the method and the ability to ship and operate \
independently, not one specific domain -- do not read "generalist" as unfocused; it \
means domain-agnostic rigor plus real production ownership. \
Reason for this over a domain-specific primary target: none of his actual paid jobs \
have ever used a data-science/research/analyst title (his research was done as a \
grad student, not an employee), so a title requiring "years of professional DS \
experience" will not be satisfied by this resume regardless of wording -- lead with \
what IS directly verifiable (shipped, operated software; published research) rather \
than a job-title match that doesn't exist in the history.

Commercial geospatial data science (added 2026-10-06, a top-weighted lane): \
geospatial / location data science in NON-defense industry -- site selection, trade-area \
and market-expansion modeling, geocoding and address data, location intelligence, \
real-estate, retail, logistics, insurance/climate-risk and agriculture analytics. \
Target titles are unleveled or mid-level ("Geospatial Data Scientist", "GIS Data \
Scientist", "Location Analytics ..."). Senior/Lead/Principal titles that state 5+ years of \
professional experience will not be met by this resume: score them low (about 35-50), \
do not hide them. Defense/intelligence imagery and GEOINT contractors (clearance, \
US-citizenship-only, mission-partner work) are the OTHER geospatial pool and fall under \
the federal exclusion below, however good the title match is. Hybrid/onsite roles outside \
commuting distance of Lynnwood, WA count against fit (remote strongly preferred).

Strong secondary lanes, all still fully in scope, not ranked below primary in any \
punitive way -- score a strong fit within any of these highly: geospatial data \
science / remote sensing (satellite/climate data fusion, GEE, GIS); AI infrastructure \
/ LLM platform / observability roles; civic and general data-science / analysis roles \
(including state and county government, per the exclusions below); and independent \
NGOs or nonprofits that are privately or foundation-funded (not functionally \
government contractors running on federal/state grants -- screen for actual funding \
source, not just 501(c)(3) status).

Hard exclusions (strategy_flag = "federal_exclude"): federal government \
agencies, or any role requiring federal security clearance processing -- \
DOE/DOD/intelligence-community-adjacent work, national laboratories, or any \
listing that is itself a direct USAJOBS-style federal civil-service posting \
(USGS, NOAA, NASA, EPA, USDA, NPS, or any other federal department/agency). \
State, county, and municipal government roles are NOT excluded -- only federal.

Other dealbreakers (strategy_flag = "dealbreaker"): heavy international \
travel (8+ trips/year mentioned in the listing).

Preferences (factor into fit_score, not a hard exclude): remote strongly \
preferred over onsite; hybrid acceptable; comp band should be reasonable for \
a data science / analyst role (rough floor ~$70k unless clearly junior/entry \
and otherwise a strong fit). Part-time roles are explicitly acceptable and \
should NOT be penalized for lower nominal/total comp caused only by reduced \
hours -- judge comp on an hourly/pro-rated basis, not the raw annual total.
"""

SYSTEM_PROMPT = f"""You are scoring a single job listing for fit against a candidate's \
job-search strategy. Respond with ONLY a JSON object, no other text, no markdown \
fences: {{"fit_score": <0-100 integer>, "rationale": "<=200 chars>", "strategy_flag": \
"ok" | "federal_exclude" | "dealbreaker"}}.

Strategy:
{STRATEGY}
"""

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'applications.db')
MODEL = "claude-haiku-4-5-20251001"


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


def _client():
    import anthropic
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ("ai-glue-proxy" if base_url else None)
    if not api_key:
        raise RuntimeError(
            "Set ANTHROPIC_API_KEY (or ANTHROPIC_BASE_URL to route through an ai_glue proxy) in .env"
        )
    # Accept-Encoding: identity works around an ai_glue proxy bug -- it strips the
    # response's Content-Encoding header without decompressing the body, which
    # corrupts any compressed response. Forcing identity means nothing is ever
    # compressed in the first place. See src/draft.py for the same fix.
    kwargs = {
        "api_key": api_key,
        "default_headers": {"X-Aiglue-Project": "rejection_matrix", "Accept-Encoding": "identity"},
    }
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


def build_user_prompt(row):
    return (
        f"Title: {row['title']}\n"
        f"Company: {row['company']}\n"
        f"Source: {row['source']}\n"
        f"Raw listing data: {(row['raw_json'] or '')[:2000]}"
    )


def score_row(client, row):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(row)}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response truncated at max_tokens")
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError(f"no text block in response (blocks: {[b.type for b in resp.content]})")
    text = "".join(text_blocks).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def score_pending(conn, limit=None, dry_run=False):
    query = "SELECT * FROM weekly_search_results WHERE fit_score IS NULL ORDER BY id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()

    if dry_run:
        if not rows:
            print("no pending rows to score")
            return 0
        print("--- system prompt ---")
        print(SYSTEM_PROMPT)
        print("--- user prompt (row #%d) ---" % rows[0]['id'])
        print(build_user_prompt(rows[0]))
        return 0

    if not rows:
        return 0

    client = _client()
    scored = 0
    for row in rows:
        try:
            result = score_row(client, row)
        except Exception as e:
            print(f"  score failed for #{row['id']} ({row['title']}): {e}")
            continue

        hidden = 1 if result.get("strategy_flag") != "ok" else 0
        conn.execute(
            "UPDATE weekly_search_results SET fit_score = ?, fit_rationale = ?, "
            "strategy_flag = ?, hidden = CASE WHEN ? = 1 THEN 1 ELSE hidden END WHERE id = ?",
            (result.get("fit_score"), result.get("rationale"), result.get("strategy_flag"),
             hidden, row['id']),
        )
        conn.commit()
        scored += 1
        flag = f" [{result.get('strategy_flag')} -> hidden]" if hidden else ""
        print(f"  #{row['id']} {row['title']} @ {row['company']}: {result.get('fit_score')}{flag}")

    print(f"scored {scored}/{len(rows)} pending rows")
    return scored


CAPABILITY_MODEL = "claude-haiku-4-5-20251001"
RESUME_CONTEXT_PATH = os.path.expanduser('~/coding/contexts/RESUME_CONTEXT.md')

CAPABILITY_SYSTEM_PROMPT = """You are scoring a candidate's real capability fit against a single \
job listing, using RESUME_CONTEXT.md as the only source of truth for what the candidate has \
actually done. This is NOT the strategy-fit check (whether the ROLE TYPE matches the candidate's \
stated job-search preferences, scored separately) -- this is: given what the listing actually \
requires, how well does the candidate's real, verified experience cover it?

Weigh the VERIFIED CAPABILITY BOUNDARIES table heavily -- a listing that hinges on a named gap \
(e.g. requires a solver, Bayesian modeling, Tableau, an experimentation platform, years of \
domain experience the candidate lacks) should score low even if the role type otherwise matches \
the strategy. That table lists overclaim TRAPS, not a complete skills inventory -- it does NOT \
mean a skill missing from it is a gap. Before citing "no SQL/database experience" or "no cloud \
platform experience" as a gap, check the Tech Stack Summary tables and each project's own \
description (e.g. Merascope, GeoGastronomy) further down the document -- real, current production \
experience with PostgreSQL, Google Earth Engine, and AWS S3 is documented there, not in the \
boundaries table. Do not invent capabilities RESUME_CONTEXT.md doesn't document, and do not invent \
gaps beyond what it documents either.

Respond with ONLY a JSON object, no other text, no markdown fences: \
{"capability_score": <0-100 integer>, "rationale": "<=200 chars, name the biggest real gap or strength"}.
"""


def score_capability(conn, row_id):
    """On-demand capability-fit score for one listing -- reads RESUME_CONTEXT.md's
    VERIFIED CAPABILITY BOUNDARIES, not just the strategy text score_pending() uses.
    Called from the UI when Simon is interested in a specific listing, not run
    automatically for every row (an LLM call per listing isn't free)."""
    row = conn.execute("SELECT * FROM weekly_search_results WHERE id = ?", (row_id,)).fetchone()
    if not row:
        raise ValueError(f"no weekly_search_results row with id {row_id}")

    resume_context = (open(RESUME_CONTEXT_PATH).read() if os.path.exists(RESUME_CONTEXT_PATH)
                       else "(RESUME_CONTEXT.md not found -- proceed with maximum caution about unverified claims)")

    user_prompt = (
        f"Title: {row['title']}\n"
        f"Company: {row['company']}\n"
        f"Raw listing data: {row['raw_json'] or ''}\n\n"
        f"RESUME_CONTEXT.md (ground truth)\n{resume_context}"
    )

    client = _client()
    resp = client.messages.create(
        model=CAPABILITY_MODEL,
        max_tokens=400,
        system=CAPABILITY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response truncated at max_tokens")
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError(f"no text block in response (blocks: {[b.type for b in resp.content]})")
    text = "".join(text_blocks).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    result = json.loads(text)

    conn.execute(
        "UPDATE weekly_search_results SET capability_score = ?, capability_rationale = ? WHERE id = ?",
        (result.get("capability_score"), result.get("rationale"), row_id),
    )
    conn.commit()
    return result


CAPABILITY_HIDE_THRESHOLD = 50


def score_capability_pending(conn, limit=None, hide_below=CAPABILITY_HIDE_THRESHOLD):
    """Run score_capability() on every row that doesn't have one yet -- called from
    the daily pipeline now, not just the /uncovered button, so nothing requires a
    manual click to reach a visible score. Skips rows already hidden by strategy
    (federal_exclude/dealbreaker) -- no point spending a capability call on those.
    Anything scoring below hide_below is hidden the same way a strategy exclusion
    or the manual Hide button does (hidden=1); capability_rationale explains why."""
    query = "SELECT * FROM weekly_search_results WHERE capability_score IS NULL AND hidden = 0 ORDER BY id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    if not rows:
        return 0

    scored = 0
    for row in rows:
        try:
            result = score_capability(conn, row['id'])
        except Exception as e:
            print(f"  capability score failed for #{row['id']} ({row['title']}): {e}")
            continue
        scored += 1
        cap = result.get("capability_score")
        if cap is not None and cap < hide_below:
            conn.execute("UPDATE weekly_search_results SET hidden = 1 WHERE id = ?", (row['id'],))
            conn.commit()
            print(f"  #{row['id']} {row['title']} @ {row['company']}: capability {cap} [below {hide_below} -> hidden]")
        else:
            print(f"  #{row['id']} {row['title']} @ {row['company']}: capability {cap}")

    print(f"capability-scored {scored}/{len(rows)} pending rows")
    return scored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _load_env()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        score_pending(conn, limit=args.limit, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
