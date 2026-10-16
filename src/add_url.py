#!/usr/bin/env python3
"""
Add one job listing to the Uncovered tab from a URL (or pasted text).

Fetches the page, has Haiku pull out title/company/etc., inserts a
weekly_search_results row (source='manual'), then runs the same strategy-fit and
capability scores the pipeline uses. Called from POST /uncovered/add_url, which
lab_kb hits from Simon's phone.

Usage: python3 add_url.py <url>
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from score import _client, MODEL, score_row, score_capability, _load_env

_load_env()

EXTRACT_PROMPT = """Extract the job listing from this web page text. Respond with ONLY a JSON \
object, no markdown fences: {"title": str, "company": str, "location": str, "type": \
"remote"|"hybrid"|"onsite"|"unknown", "sal_lo": int|null, "sal_hi": int|null, "description": \
"<=1500 chars: responsibilities, requirements, qualifications, verbatim where possible"}. \
If the page is not a job listing (login wall, error page, search results), respond \
{"error": "<why>"}."""


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read(2_000_000).decode("utf-8", "replace")
    # schema.org JobPosting JSON-LD is the cleanest source when a site has it
    ld = re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
    body = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'\s+', ' ', body)
    return ' '.join(ld)[:6000] + '\n' + body[:12000]


def extract(client, text):
    resp = client.messages.create(model=MODEL, max_tokens=1500, system=EXTRACT_PROMPT,
                                  messages=[{"role": "user", "content": text}])
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", out, flags=re.M).strip())


def add_job(conn, url, text=None):
    """Returns {ok, id, title, company, fit_score, capability_score, ...} or {ok: False, error}."""
    dup = conn.execute("SELECT id, title, company FROM weekly_search_results WHERE url = ?", (url,)).fetchone()
    if dup:
        return {"ok": False, "error": f"already in Uncovered as #{dup['id']} ({dup['title']} @ {dup['company']})"}

    client = _client()
    if not text:
        try:
            text = fetch_text(url)
        except Exception as e:
            return {"ok": False, "error": f"fetch failed ({e}); resend with the listing text pasted in"}
    job = extract(client, text)
    if job.get("error") or not job.get("title"):
        return {"ok": False, "error": job.get("error") or "no title found; resend with the listing text pasted in"}
    job.update(url=url, source="manual")

    cur = conn.execute(
        "INSERT INTO weekly_search_results (run_date, title, company, url, source, hidden, raw_json) "
        "VALUES (?, ?, ?, ?, 'manual', 0, ?)",
        (datetime.utcnow().isoformat(), job["title"], job.get("company"), url, json.dumps(job)))
    conn.commit()
    row_id = cur.lastrowid
    out = {"ok": True, "id": row_id, "title": job["title"], "company": job.get("company"),
           "location": job.get("location")}

    # Manual adds are never auto-hidden, even on a federal flag: Simon chose this one. Scores are advisory.
    try:
        row = conn.execute("SELECT * FROM weekly_search_results WHERE id = ?", (row_id,)).fetchone()
        r = score_row(client, row)
        conn.execute("UPDATE weekly_search_results SET fit_score=?, fit_rationale=?, strategy_flag=? WHERE id=?",
                     (r.get("fit_score"), r.get("rationale"), r.get("strategy_flag"), row_id))
        conn.commit()
        out.update(fit_score=r.get("fit_score"), fit_rationale=r.get("rationale"), strategy_flag=r.get("strategy_flag"))
        c = score_capability(conn, row_id)
        out.update(capability_score=c.get("capability_score"), capability_rationale=c.get("rationale"))
    except Exception as e:
        out["score_error"] = str(e)
    return out


if __name__ == "__main__":
    from rm import get_db
    with get_db() as db:
        print(json.dumps(add_job(db, sys.argv[1]), indent=2))
