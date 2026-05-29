#!/usr/bin/env python3
"""
rejection_matrix — job search CLI.

Searches Remotive, We Work Remotely, and The Muse without API keys.

Job boards searched:
  Remotive         https://remotive.com          — remote jobs; salary data; no key needed
  We Work Remotely https://weworkremotely.com    — remote jobs; no salary; no key needed
  The Muse         https://www.themuse.com        — general; location filter; no key needed
  JSearch          via RapidAPI                  — aggregates LinkedIn, Indeed, Glassdoor,
                                                   ZipRecruiter; requires JSEARCH_API_KEY env var
  USAJOBS          https://www.usajobs.gov        — US federal jobs (NPS, USGS, NOAA, etc.);
                                                   requires USAJOBS_API_KEY + USAJOBS_EMAIL env vars
  Salesforce       https://careers.salesforce.com — scraped; no key needed; keyword-filtered

JSearch is included automatically when JSEARCH_API_KEY is set; skipped otherwise.
Get a free key (200 req/month) at: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch

USAJOBS is included automatically when USAJOBS_API_KEY and USAJOBS_EMAIL are set; skipped otherwise.
Get a free key at: https://developer.usajobs.gov/APIRequest/Index

Each result shows the board it came from and a direct URL to the listing.
When saved with --save, the board name goes into the 'source' field and the
URL goes into 'notes' in the tracker DB.

Usage:
  search.py <keywords...> [--zip ZIP] [--radius MILES]
            [--salary-min N] [--salary-max N]
            [--remote] [--hybrid]
            [--industry INDUSTRY] [--company COMPANY]
            [--sources remotive,wwr,muse,jsearch,usajobs] [--limit N] [--save]

Examples:
  python3 src/search.py "data analyst"
  python3 src/search.py "software engineer" --zip 98101 --salary-min 120000
  python3 src/search.py "product manager" --remote --hybrid --salary-min 100000 --save
  python3 src/search.py "data engineer" --industry fintech --sources remotive,wwr
  python3 src/search.py "engineer" --company Google --remote
  python3 src/search.py "park ranger" --zip 98087 --sources usajobs
  python3 src/search.py "geologist" --zip 98087 --sources usajobs --remote
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'applications.db')

RESET  = '\033[0m'
BOLD   = '\033[1m'
DIM    = '\033[90m'
GREEN  = '\033[32m'
YELLOW = '\033[33m'
CYAN   = '\033[36m'
RED    = '\033[31m'

ALL_SOURCES = ['remotive', 'wwr', 'muse', 'jsearch', 'usajobs', 'noaa', 'nasa', 'usgs', 'salesforce', 'ramp']


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _request(url, timeout=15):
    req = urllib.request.Request(
        url, headers={'User-Agent': 'rejection-matrix-search/1.0 (personal job tracker)'}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def fetch_json(url, timeout=15):
    return json.loads(_request(url, timeout))


def fetch_text(url, timeout=15):
    return _request(url, timeout)


# ── location ───────────────────────────────────────────────────────────────────

def zip_to_city(zipcode):
    """Resolve US zip → (city, state_abbr) via zippopotam.us — free, no auth."""
    try:
        data = fetch_json(f"https://api.zippopotam.us/us/{zipcode}", timeout=8)
        p = data['places'][0]
        return p['place name'], p['state abbreviation']
    except Exception:
        return None, None


# ── salary parsing ─────────────────────────────────────────────────────────────

def parse_salary(text):
    """
    Extract (min_annual, max_annual) from free-text salary strings.
    Returns (None, None) when salary can't be determined.
    """
    if not text:
        return None, None
    # Skip hourly rates
    if re.search(r'/\s*h(r|our)?', text.lower()):
        return None, None

    nums = []
    # Match: optional $, integer with optional comma-groups, optional k/K suffix
    for m in re.finditer(r'\$?((?:\d{1,3}(?:,\d{3})+|\d+))([kK])?', text):
        raw = m.group(1).replace(',', '')
        n = float(raw)
        if m.group(2):          # explicit 'k' suffix
            n *= 1000
        elif n < 1000:          # bare small number — treat as thousands
            n *= 1000
        if 20_000 <= n <= 1_000_000:
            nums.append(int(n))

    nums = sorted(set(nums))
    if len(nums) >= 2:
        return nums[0], nums[-1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return None, None


def salary_in_range(sal_lo, sal_hi, want_min, want_max):
    """Return False only when we know the listing is definitely outside range."""
    if want_min and sal_hi is not None and sal_hi < want_min:
        return False
    if want_max and sal_lo is not None and sal_lo > want_max:
        return False
    return True


# ── display helpers ─────────────────────────────────────────────────────────────

def fmt_salary(lo, hi):
    if lo is None and hi is None:
        return '—'
    if lo is None:
        return f"up to ${hi:,}"
    if hi is None or lo == hi:
        return f"${lo:,}"
    return f"${lo:,}–${hi:,}"


def fmt_type(t, width=7):
    colors = {'remote': GREEN, 'hybrid': YELLOW, 'onsite': CYAN}
    c = colors.get(t, '')
    padded = t.ljust(width)
    return f"{c}{padded}{RESET}" if c else padded


def infer_work_type(title, location, description=''):
    combined = f"{title} {location} {description}".lower()
    if 'remote' in combined:
        return 'remote'
    if 'hybrid' in combined or 'flexible' in combined:
        return 'hybrid'
    if location and location.lower() not in ('', 'worldwide', 'anywhere'):
        return 'onsite'
    return 'remote'  # assume remote if no location clues


def keywords_match(title, description, keywords):
    haystack = f"{title} {description}".lower()
    return all(kw.lower() in haystack for kw in keywords)


# ── sources ────────────────────────────────────────────────────────────────────

def source_remotive(keywords, salary_min, salary_max, limit, industry=None):
    print('  Remotive...', end='', flush=True)
    try:
        params = {'search': ' '.join(keywords), 'limit': limit}
        if industry:
            params['category'] = industry
        url = 'https://remotive.com/api/remote-jobs?' + urllib.parse.urlencode(params)
        data = fetch_json(url)
        results = []
        for j in data.get('jobs', []):
            if not keywords_match(j.get('title', ''), j.get('description', ''), keywords):
                continue
            sal_text = j.get('salary') or ''
            sal_lo, sal_hi = parse_salary(sal_text)
            if not salary_in_range(sal_lo, sal_hi, salary_min, salary_max):
                continue
            results.append({
                'title':   j.get('title', ''),
                'company': j.get('company_name', ''),
                'location': j.get('candidate_required_location') or 'Worldwide',
                'type':    'remote',
                'sal_lo':  sal_lo,
                'sal_hi':  sal_hi,
                'sal_raw': sal_text,
                'url':     j.get('url', ''),
                'source':  'Remotive',
                'posted':  (j.get('publication_date') or '')[:10],
            })
        print(f" {len(results)}")
        return results
    except Exception as e:
        print(f" FAILED ({e})")
        return []


def source_wwr(keywords, salary_min, salary_max):
    print('  We Work Remotely...', end='', flush=True)
    try:
        xml_text = fetch_text('https://weworkremotely.com/remote-jobs.rss')
        root = ET.fromstring(xml_text)
        ns = 'https://weworkremotely.com'

        results = []
        for item in root.findall('.//item'):
            def el(tag):
                node = item.find(tag)
                return (node.text or '').strip() if node is not None else ''

            raw_title = el('title')
            # WWR titles: "Company Name: Job Title: Region" or similar
            parts = [p.strip() for p in raw_title.split(':')]
            if len(parts) >= 3:
                company = parts[0]
                title   = parts[1]
            elif len(parts) == 2:
                company = parts[0]
                title   = parts[1]
            else:
                company = el(f'{{{ns}}}company')
                title   = raw_title

            region  = el(f'{{{ns}}}region') or 'Remote'
            link    = el('link')
            pubdate = el('pubDate')[:16]

            if not keywords_match(title, '', keywords):
                continue

            results.append({
                'title':   title,
                'company': company,
                'location': region,
                'type':    'remote',
                'sal_lo':  None,
                'sal_hi':  None,
                'sal_raw': '',
                'url':     link,
                'source':  'WWR',
                'posted':  pubdate,
            })
        print(f" {len(results)}")
        return results
    except Exception as e:
        print(f" FAILED ({e})")
        return []


def source_muse(keywords, location_city, salary_min, salary_max, limit, industry=None):
    print('  The Muse...', end='', flush=True)
    try:
        results = []
        max_pages = max(1, min(limit // 20, 5))  # up to 5 pages, 20 results each
        for page in range(max_pages):
            params = {'page': page, 'descending': 'true'}
            if location_city:
                params['location'] = location_city
            if industry:
                params['category'] = industry
            url = 'https://www.themuse.com/api/public/jobs?' + urllib.parse.urlencode(params)
            data = fetch_json(url)
            page_results = data.get('results', [])
            if not page_results:
                break
            for j in page_results:
                title = j.get('name', '')
                if not keywords_match(title, '', keywords):
                    continue
                locs    = [loc.get('name', '') for loc in j.get('locations', [])]
                loc_str = ', '.join(locs)
                wtype   = infer_work_type(title, loc_str)
                results.append({
                    'title':   title,
                    'company': (j.get('company') or {}).get('name', ''),
                    'location': loc_str or 'Unknown',
                    'type':    wtype,
                    'sal_lo':  None,
                    'sal_hi':  None,
                    'sal_raw': '',
                    'url':     (j.get('refs') or {}).get('landing_page', ''),
                    'source':  'The Muse',
                    'posted':  (j.get('publication_date') or '')[:10],
                })
            if len(page_results) < 20:
                break
        print(f" {len(results)}")
        return results
    except Exception as e:
        print(f" FAILED ({e})")
        return []


def source_jsearch(keywords, location_city, salary_min, salary_max, limit,
                   remote_only=False, industry=None):
    api_key = os.environ.get('JSEARCH_API_KEY', '').strip()
    if not api_key:
        print('  JSearch (LinkedIn/Indeed/Glassdoor)... SKIPPED (set JSEARCH_API_KEY)')
        return []

    print('  JSearch (LinkedIn/Indeed/Glassdoor)...', end='', flush=True)
    try:
        # JSearch understands natural-language queries — location and remote go in the string.
        # Use Seattle as the metro anchor (JSearch geocodes major cities reliably;
        # the Puget Sound filter in app.py handles the actual radius constraint).
        query_parts = list(keywords)
        if industry:
            query_parts.append(industry)
        if remote_only:
            query_parts.append('remote')
        elif location_city:
            query_parts.append('in Seattle, WA')

        params = {
            'query':     ' '.join(query_parts),
            'page':      '1',
            'num_pages': str(max(1, min(limit // 10, 3))),
        }
        if remote_only:
            params['remote_jobs_only'] = 'true'

        url = 'https://jsearch.p.rapidapi.com/search?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            'User-Agent':        'rejection-matrix-search/1.0',
            'X-RapidAPI-Key':    api_key,
            'X-RapidAPI-Host':   'jsearch.p.rapidapi.com',
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        results = []
        for j in data.get('data', []):
            sal_lo     = j.get('job_min_salary')
            sal_hi     = j.get('job_max_salary')
            sal_period = (j.get('job_salary_period') or '').upper()

            # Normalise to annual
            if sal_lo is not None:
                if sal_period == 'HOUR':
                    sal_lo = int(sal_lo * 2080)
                    sal_hi = int(sal_hi * 2080) if sal_hi else sal_lo
                elif sal_period == 'MONTH':
                    sal_lo = int(sal_lo * 12)
                    sal_hi = int(sal_hi * 12) if sal_hi else sal_lo
                else:
                    sal_lo = int(sal_lo)
                    sal_hi = int(sal_hi) if sal_hi else sal_lo

            if not salary_in_range(sal_lo, sal_hi, salary_min, salary_max):
                continue

            city   = j.get('job_city')  or ''
            state  = j.get('job_state') or ''
            loc    = ', '.join(filter(None, [city, state]))
            is_rem = j.get('job_is_remote', False)
            if not loc:
                loc = 'Remote' if is_rem else 'Unknown'
            wtype  = 'remote' if is_rem else infer_work_type(j.get('job_title', ''), loc)

            # job_publisher is the underlying board: LinkedIn, Indeed, Glassdoor, etc.
            publisher = j.get('job_publisher') or 'JSearch'
            apply_url = j.get('job_apply_link') or j.get('job_google_link', '')

            results.append({
                'title':   j.get('job_title', ''),
                'company': j.get('employer_name', ''),
                'location': loc,
                'type':    wtype,
                'sal_lo':  sal_lo,
                'sal_hi':  sal_hi,
                'sal_raw': '',
                'url':     apply_url,
                'source':  publisher,
                'posted':  (j.get('job_posted_at_datetime_utc') or '')[:10],
            })

        print(f" {len(results)}")
        return results

    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(' RATE LIMITED (200 req/month on free tier)')
        elif e.code == 403:
            print(' INVALID KEY — check JSEARCH_API_KEY')
        else:
            print(f' FAILED (HTTP {e.code})')
        return []
    except Exception as e:
        print(f' FAILED ({e})')
        return []


def source_usajobs(keywords, location_city, salary_min, salary_max, limit,
                   remote_only=False, org_filter=None, source_label=None, radius=50):
    """
    Search USAJOBS.

    org_filter: if set, only return results whose OrganizationName contains
                this substring (case-insensitive). Enables paginated fetch
                (up to 2500 results) so agency-specific jobs aren't buried.
    source_label: display name — defaults to 'USAJOBS'.
    radius: miles around location_city (only applied when location_city is set).
    """
    api_key = os.environ.get('USAJOBS_API_KEY', '').strip()
    email   = os.environ.get('USAJOBS_EMAIL', '').strip()
    label   = source_label or 'USAJOBS'

    if not api_key or not email:
        print(f'  {label}... SKIPPED (set USAJOBS_API_KEY and USAJOBS_EMAIL)')
        return []

    print(f'  {label}...', end='', flush=True)
    try:
        # When filtering by org, fetch large pages across up to 5 pages so we
        # don't miss agency jobs buried in high-volume keyword results.
        per_page  = 500 if org_filter else min(limit, 500)
        max_pages = 5   if org_filter else 1

        results = []
        org_lower = org_filter.lower() if org_filter else None

        for page in range(1, max_pages + 1):
            params = {
                'Keyword':        ' '.join(keywords),
                'ResultsPerPage': per_page,
                'Page':           page,
                'WhoMayApply':    'Public',
            }
            if location_city:
                params['LocationName'] = location_city
                params['Radius'] = radius
            if remote_only:
                params['RemoteIndicator'] = 'True'

            url = 'https://data.usajobs.gov/api/search?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={
                'Host':              'data.usajobs.gov',
                'User-Agent':        email,
                'Authorization-Key': api_key,
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            items = data.get('SearchResult', {}).get('SearchResultItems', [])

            for item in items:
                j = item.get('MatchedObjectDescriptor', {})

                # Org filter
                if org_lower:
                    org_name = (j.get('OrganizationName') or '').lower()
                    if org_lower not in org_name:
                        continue

                # Salary
                sal_lo, sal_hi = None, None
                for rem in j.get('PositionRemuneration', []):
                    lo_raw  = rem.get('MinimumRange')
                    hi_raw  = rem.get('MaximumRange')
                    period  = (rem.get('RateIntervalCode') or '').lower()
                    if lo_raw:
                        lo = float(lo_raw)
                        hi = float(hi_raw) if hi_raw else lo
                        if 'hour' in period:
                            lo, hi = int(lo * 2080), int(hi * 2080)
                        elif 'month' in period:
                            lo, hi = int(lo * 12), int(hi * 12)
                        else:
                            lo, hi = int(lo), int(hi)
                        sal_lo, sal_hi = lo, hi
                        break

                if not salary_in_range(sal_lo, sal_hi, salary_min, salary_max):
                    continue

                locs    = j.get('PositionLocation', [])
                loc     = locs[0].get('LocationName', '') if locs else ''
                if not loc:
                    loc = 'Remote' if remote_only else 'Unknown'

                apply_uris = j.get('ApplyURI', [])
                url_out    = apply_uris[0] if apply_uris else ''

                wtype = 'remote' if remote_only else infer_work_type(
                    j.get('PositionTitle', ''), loc
                )

                results.append({
                    'title':   j.get('PositionTitle', ''),
                    'company': j.get('OrganizationName', '') or j.get('DepartmentName', ''),
                    'location': loc,
                    'type':    wtype,
                    'sal_lo':  sal_lo,
                    'sal_hi':  sal_hi,
                    'sal_raw': '',
                    'url':     url_out,
                    'source':  label,
                    'posted':  (j.get('PublicationStartDate') or '')[:10],
                })

            if len(results) >= limit or len(items) < per_page:
                break

        print(f" {len(results)}")
        return results[:limit]

    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f' INVALID KEY — check USAJOBS_API_KEY and USAJOBS_EMAIL')
        else:
            print(f' FAILED (HTTP {e.code})')
        return []
    except Exception as e:
        print(f' FAILED ({e})')
        return []


def source_noaa(keywords, location_city, salary_min, salary_max, limit, remote_only=False, radius=50):
    return source_usajobs(keywords, location_city, salary_min, salary_max, limit,
                          remote_only=remote_only, radius=radius,
                          org_filter='National Oceanic',
                          source_label='NOAA')


def source_nasa(keywords, location_city, salary_min, salary_max, limit, remote_only=False, radius=50):
    return source_usajobs(keywords, location_city, salary_min, salary_max, limit,
                          remote_only=remote_only, radius=radius,
                          org_filter='NASA',
                          source_label='NASA')


def source_usgs(keywords, location_city, salary_min, salary_max, limit, remote_only=False, radius=50):
    return source_usajobs(keywords, location_city, salary_min, salary_max, limit,
                          remote_only=remote_only, radius=radius,
                          org_filter='Geological Survey',
                          source_label='USGS')


def _deslug(slug):
    """Convert URL slug like 'senior-data-scientist' → 'Senior Data Scientist'."""
    return ' '.join(w.capitalize() for w in slug.split('-'))


def source_salesforce(keywords, limit):
    """
    Search careers.salesforce.com via sitemap + individual job pages.

    Strategy:
      1. Fetch sitemap.xml once to get all ~1,380 job URLs (job_id + slug).
      2. Keyword-filter using deslugified slug (fast, zero extra requests).
      3. Fetch individual job pages for up to `limit` matches to get real
         title and location from the <title> tag.

    Individual page <title> format: "Job Title, Location | Salesforce Careers"
    """
    print('  Salesforce...', end='', flush=True)
    try:
        base = 'https://careers.salesforce.com'

        # 1. Fetch sitemap and extract all job URLs
        sitemap_xml = fetch_text(f'{base}/sitemap.xml')
        job_urls = re.findall(
            r'<loc>(https://careers\.salesforce\.com/en/jobs/(jr\d+)/([^/<]+)/)</loc>',
            sitemap_xml
        )
        if not job_urls:
            print(' FAILED (no jobs found in sitemap)')
            return []

        # 2. Keyword-filter by deslugified slug
        candidates = []
        for full_url, job_id, slug in job_urls:
            title_guess = _deslug(slug)
            if keywords_match(title_guess, '', keywords):
                candidates.append((full_url, job_id, slug))
            if len(candidates) >= limit:
                break

        if not candidates:
            print(f' 0')
            return []

        # 3. Fetch individual pages for real title + location
        results = []
        for full_url, job_id, slug in candidates[:limit]:
            try:
                html = fetch_text(full_url)
            except Exception:
                # Fall back to deslugified title, no location
                title = _deslug(slug)
                results.append({
                    'title':   title,
                    'company': 'Salesforce',
                    'location': 'Salesforce',
                    'type':    infer_work_type(title, ''),
                    'sal_lo':  None,
                    'sal_hi':  None,
                    'sal_raw': '',
                    'url':     full_url,
                    'source':  'Salesforce',
                    'posted':  '',
                })
                continue

            # Parse "<Title>, <Location> | Salesforce Careers"
            page_title_m = re.search(r'<title>([^<]+)</title>', html)
            if page_title_m:
                raw = page_title_m.group(1).split('|')[0].strip()
                # Split on last comma to separate title from location
                parts = raw.rsplit(',', 1)
                if len(parts) == 2:
                    title, loc = parts[0].strip(), parts[1].strip()
                else:
                    title, loc = raw, 'Salesforce'
            else:
                title = _deslug(slug)
                loc   = 'Salesforce'

            # Unescape HTML entities in title
            title = re.sub(r'&amp;', '&', title)
            title = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), title)

            results.append({
                'title':   title,
                'company': 'Salesforce',
                'location': loc,
                'type':    infer_work_type(title, loc),
                'sal_lo':  None,
                'sal_hi':  None,
                'sal_raw': '',
                'url':     full_url,
                'source':  'Salesforce',
                'posted':  '',
            })

        print(f' {len(results)}')
        return results

    except Exception as e:
        print(f' FAILED ({e})')
        return []


def source_ramp(keywords, salary_min, salary_max, limit):
    """
    Search Ramp's careers page (jobs.ashbyhq.com/ramp).

    Ramp uses Ashby ATS which embeds all job listings as JSON in the page HTML.
    No API key required.
    """
    print('  Ramp...', end='', flush=True)
    try:
        html = fetch_text('https://jobs.ashbyhq.com/ramp')

        # All job data lives in one <script> tag as a JSON blob
        script_matches = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        jobs_json = None
        for s in script_matches:
            if '"jobPostings":[' not in s:
                continue
            idx = s.find('"jobPostings":[')
            start = idx + len('"jobPostings":')
            depth = 0
            end = start
            for k, c in enumerate(s[start:]):
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        end = start + k + 1
                        break
            jobs_json = json.loads(s[start:end])
            break

        if jobs_json is None:
            print(' FAILED (could not find job data in page)')
            return []

        _wtype = {'Remote': 'remote', 'Hybrid': 'hybrid', 'OnSite': 'onsite'}

        results = []
        for j in jobs_json:
            if not j.get('isListed', True):
                continue
            title = (j.get('title') or '').strip()
            if not keywords_match(title, '', keywords):
                continue

            sal_text = j.get('compensationTierSummary') or ''
            sal_lo, sal_hi = parse_salary(sal_text)
            if not salary_in_range(sal_lo, sal_hi, salary_min, salary_max):
                continue

            wtype = _wtype.get(j.get('workplaceType', ''), 'onsite')
            loc   = j.get('locationName') or ''
            url   = f"https://jobs.ashbyhq.com/ramp/{j['id']}"

            results.append({
                'title':   title,
                'company': 'Ramp',
                'location': loc,
                'type':    wtype,
                'sal_lo':  sal_lo,
                'sal_hi':  sal_hi,
                'sal_raw': sal_text,
                'url':     url,
                'source':  'Ramp',
                'posted':  (j.get('publishedDate') or '')[:10],
            })
            if len(results) >= limit:
                break

        print(f' {len(results)}')
        return results

    except Exception as e:
        print(f' FAILED ({e})')
        return []


# ── display ────────────────────────────────────────────────────────────────────

def display_results(results):
    if not results:
        print(f'\n{YELLOW}No results.{RESET}')
        return

    w_n    = len(str(len(results)))
    w_co   = min(max(len(r['company']) for r in results), 28)
    w_ttl  = min(max(len(r['title'])   for r in results), 42)
    w_loc  = min(max(len(r['location'])for r in results), 22)
    w_sal  = max(max(len(fmt_salary(r['sal_lo'], r['sal_hi'])) for r in results), 6)
    w_src  = max(max(len(r['source'])  for r in results), 6)
    w_type = 7  # 'unknown' is 7 chars

    header = (
        f"{'#':<{w_n}}  "
        f"{'Company':<{w_co}}  "
        f"{'Title':<{w_ttl}}  "
        f"{'Location':<{w_loc}}  "
        f"{'Salary':<{w_sal}}  "
        f"{'Type':<{w_type}}  "
        f"{'Source':<{w_src}}  "
        f"URL"
    )
    sep_len = len(header) + 10
    print(f"\n{BOLD}{header}{RESET}")
    print('─' * min(sep_len, 140))

    for i, r in enumerate(results, 1):
        co    = (r['company']  or '')[:w_co].ljust(w_co)
        ttl   = (r['title']    or '')[:w_ttl].ljust(w_ttl)
        loc   = (r['location'] or '')[:w_loc].ljust(w_loc)
        sal   = fmt_salary(r['sal_lo'], r['sal_hi']).ljust(w_sal)
        typ   = fmt_type(r['type'], w_type)
        src   = (r['source']   or '').ljust(w_src)
        url   = r['url'] or ''

        print(
            f"{str(i):<{w_n}}  "
            f"{co}  "
            f"{ttl}  "
            f"{loc}  "
            f"{sal}  "
            f"{typ}  "
            f"{src}  "
            f"{DIM}{url}{RESET}"
        )

    print(f"\n{len(results)} result(s)")


# ── save to tracker ────────────────────────────────────────────────────────────

def save_to_tracker(result):
    if not os.path.exists(DB_PATH):
        print(f"{RED}Tracker DB not found at {DB_PATH}{RESET}")
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    salary = result['sal_lo']
    loc    = result['location'] if result['location'] not in ('', 'Unknown') else result['type']
    notes  = result['url'] or ''
    cur = conn.execute(
        """INSERT INTO job_applications
           (role, company, date_applied, status, source, location, salary_offer, notes)
           VALUES (?, ?, ?, 'applied', ?, ?, ?, ?)""",
        (result['title'], result['company'], date.today().isoformat(),
         result['source'], loc, salary, notes),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"  {GREEN}Saved #{row_id}:{RESET} {result['title']} @ {result['company']}")
    return row_id


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='search.py',
        description='Search job boards and optionally save results to the tracker.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.strip().split('Examples:')[1].strip() if 'Examples:' in __doc__ else '',
    )
    parser.add_argument('keywords', nargs='+',
                        help='Job title keywords (quote multi-word phrases)')
    parser.add_argument('--zip',        metavar='ZIP',
                        help='US zip code — resolves to city/state for location filter')
    parser.add_argument('--location',   metavar='LOCATION',
                        help='Free-text location passed directly to JSearch/The Muse (e.g. "Seattle, WA"). '
                             'Alternative to --zip. Set JSEARCH_DEFAULT_LOCATION env var to avoid typing it every time.')
    parser.add_argument('--radius',     type=int, default=25, metavar='MILES',
                        help='Radius in miles around --zip (default 25; used for onsite/hybrid)')
    parser.add_argument('--salary-min', type=int, dest='salary_min', metavar='N',
                        help='Exclude listings with known max salary below this')
    parser.add_argument('--salary-max', type=int, dest='salary_max', metavar='N',
                        help='Exclude listings with known min salary above this')
    parser.add_argument('--remote',     action='store_true',
                        help='Include remote listings (default: all types shown)')
    parser.add_argument('--hybrid',     action='store_true',
                        help='Include hybrid listings (default: all types shown)')
    parser.add_argument('--industry',   metavar='INDUSTRY',
                        help='Industry/category filter passed to APIs (e.g. fintech, data, marketing)')
    parser.add_argument('--company',    metavar='NAME',
                        help='Filter results to companies whose name contains this string')
    parser.add_argument('--sources',    default=','.join(ALL_SOURCES), metavar='LIST',
                        help=f"Comma-separated sources: {', '.join(ALL_SOURCES)} (default: all). "
                             f"usajobs requires USAJOBS_API_KEY + USAJOBS_EMAIL env vars.")
    parser.add_argument('--limit',      type=int, default=25, metavar='N',
                        help='Max results to fetch per source (default 25)')
    parser.add_argument('--save',       action='store_true',
                        help='Prompt to save selected results to the tracker after display')
    args = parser.parse_args()

    sources  = [s.strip().lower() for s in args.sources.split(',') if s.strip()]
    keywords = args.keywords

    # Resolve location: --zip takes precedence, then --location, then env var default
    location_city = None
    if args.zip:
        city, state = zip_to_city(args.zip)
        if city and state:
            location_city = f"{city}, {state}"
            print(f"Zip {args.zip} → {location_city}")
        else:
            print(f"{YELLOW}Could not resolve zip {args.zip} — searching without location filter{RESET}")
    elif args.location:
        location_city = args.location
    elif os.environ.get('JSEARCH_DEFAULT_LOCATION', '').strip():
        location_city = os.environ['JSEARCH_DEFAULT_LOCATION'].strip()

    # Print search summary
    print(f"Keywords : {' '.join(keywords)}")
    if location_city:
        print(f"Location : {location_city} (±{args.radius} mi for onsite/hybrid)")
    if args.salary_min or args.salary_max:
        lo = f"${args.salary_min:,}" if args.salary_min else 'any'
        hi = f"${args.salary_max:,}" if args.salary_max else 'any'
        print(f"Salary   : {lo} – {hi}")
    if args.industry:
        print(f"Industry : {args.industry}")
    if args.company:
        print(f"Company  : {args.company}")
    print(f"Sources  : {', '.join(sources)}")
    print()

    all_results = []

    if 'remotive' in sources:
        all_results += source_remotive(keywords, args.salary_min, args.salary_max, args.limit,
                                       industry=args.industry)

    if 'wwr' in sources:
        all_results += source_wwr(keywords, args.salary_min, args.salary_max)

    if 'muse' in sources:
        all_results += source_muse(keywords, location_city, args.salary_min, args.salary_max, args.limit,
                                   industry=args.industry)

    if 'jsearch' in sources:
        all_results += source_jsearch(keywords, location_city, args.salary_min, args.salary_max,
                                      args.limit, remote_only=args.remote, industry=args.industry)

    if 'usajobs' in sources:
        all_results += source_usajobs(keywords, location_city, args.salary_min, args.salary_max,
                                      args.limit, remote_only=args.remote)

    if 'noaa' in sources:
        all_results += source_noaa(keywords, location_city, args.salary_min, args.salary_max,
                                   args.limit, remote_only=args.remote)

    if 'nasa' in sources:
        all_results += source_nasa(keywords, location_city, args.salary_min, args.salary_max,
                                   args.limit, remote_only=args.remote)

    if 'usgs' in sources:
        all_results += source_usgs(keywords, location_city, args.salary_min, args.salary_max,
                                   args.limit, remote_only=args.remote)

    if 'salesforce' in sources:
        all_results += source_salesforce(keywords, args.limit)

    if 'ramp' in sources:
        all_results += source_ramp(keywords, args.salary_min, args.salary_max, args.limit)

    # Work-type filter — only apply if at least one flag was passed
    if args.remote or args.hybrid:
        wanted = set()
        if args.remote:
            wanted.add('remote')
        if args.hybrid:
            wanted.add('hybrid')
        all_results = [r for r in all_results if r['type'] in wanted]

    # Company filter (client-side substring match)
    if args.company:
        needle = args.company.lower()
        all_results = [r for r in all_results if needle in r['company'].lower()]

    # Deduplicate by (company, title) case-insensitively
    seen, deduped = set(), []
    for r in all_results:
        key = (r['company'].lower().strip(), r['title'].lower().strip())
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # Sort: salary desc (None last), then source
    deduped.sort(key=lambda r: (-(r['sal_lo'] or 0), r['source']))

    display_results(deduped)

    if args.save and deduped:
        print('\nEnter result numbers to save to tracker (e.g. 1,3,5), or press Enter to skip:')
        raw = input('  > ').strip()
        if raw:
            for tok in raw.split(','):
                try:
                    idx = int(tok.strip()) - 1
                    if 0 <= idx < len(deduped):
                        save_to_tracker(deduped[idx])
                    else:
                        print(f"  {YELLOW}No result #{idx + 1}{RESET}")
                except ValueError:
                    pass


if __name__ == '__main__':
    main()
