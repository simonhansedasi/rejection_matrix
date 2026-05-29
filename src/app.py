import sys, os, json
from datetime import date
from flask import Flask, render_template, request, Response, redirect, url_for

sys.path.insert(0, os.path.dirname(__file__))
import search as search_mod
from rm import get_db

app = Flask(__name__)

STATUSES = ['applied', 'interviewing', 'offer', 'rejected', 'ghosted', 'dead', 'scam']

# City/region names within ~50 miles of Lynnwood, WA — used to filter onsite results
PUGET_SOUND_TERMS = frozenset({
    'washington', ' wa ', ', wa', 'seattle', 'bellevue', 'tacoma', 'everett',
    'bothell', 'lynnwood', 'kirkland', 'redmond', 'renton', 'kent', 'auburn',
    'bremerton', 'anacortes', 'mount vernon', 'marysville', 'mukilteo',
    'shoreline', 'kenmore', 'mountlake terrace', 'issaquah', 'sammamish',
    'federal way', 'burien', 'tukwila', 'seatac', 'edmonds', 'woodinville',
    'snohomish', 'monroe', 'mill creek', 'lake stevens', 'arlington', 'stanwood',
    'poulsbo', 'bainbridge', 'silverdale', 'puyallup', 'lakewood',
})
VARIANTS = ['geo', 'analytics', 'salestech', 'research', 'simulation']
STATUS_CSS = {
    'applied':      '#3b82f6',
    'interviewing': '#f59e0b',
    'offer':        '#22c55e',
    'rejected':     '#ef4444',
    'ghosted':      '#6b7280',
    'dead':         '#374151',
    'scam':         '#f97316',
}


@app.route('/')
def index():
    return render_template('search.html', variants=VARIANTS)


@app.route('/search/stream')
def search_stream():
    q           = request.args.get('q', '').strip()
    salary_min  = request.args.get('salary_min', type=int)
    salary_max  = request.args.get('salary_max', type=int)
    remote      = request.args.get('remote') == '1'
    hybrid      = request.args.get('hybrid') == '1'
    onsite      = request.args.get('onsite') == '1'
    sources     = [s.strip() for s in request.args.get('sources', 'remotive,wwr,muse,jsearch,usajobs,noaa,nasa,usgs,salesforce,ramp').split(',')]
    limit       = request.args.get('limit', 25, type=int)
    keywords    = q.split() if q else []

    # Only tell APIs to filter for remote when remote is the *only* selected work type.
    # When multiple types are checked (or none), fetch everything and let the
    # client-side filter below handle it.
    remote_only = remote and not hybrid and not onsite

    # When onsite is checked, bias location-aware sources to within 50 mi of Lynnwood.
    location_city = os.environ.get('JSEARCH_DEFAULT_LOCATION', 'Lynnwood, WA') if onsite else None

    def generate():
        if not keywords:
            yield 'data: {"event":"done"}\n\n'
            return

        seen = set()

        source_map = {
            'remotive': ('Remotive',
                lambda: search_mod.source_remotive(keywords, salary_min, salary_max, limit)),
            'wwr': ('We Work Remotely',
                lambda: search_mod.source_wwr(keywords, salary_min, salary_max)),
            'muse': ('The Muse',
                lambda: search_mod.source_muse(keywords, None, salary_min, salary_max, limit)),
            'jsearch': ('LinkedIn/Indeed/Glassdoor',
                lambda: search_mod.source_jsearch(keywords, location_city, salary_min, salary_max, limit, remote_only=remote_only)),
            'usajobs': ('USAJOBS',
                lambda: search_mod.source_usajobs(keywords, location_city, salary_min, salary_max, limit, remote_only=remote_only)),
            'noaa': ('NOAA',
                lambda: search_mod.source_noaa(keywords, location_city, salary_min, salary_max, limit, remote_only=remote_only)),
            'nasa': ('NASA',
                lambda: search_mod.source_nasa(keywords, location_city, salary_min, salary_max, limit, remote_only=remote_only)),
            'usgs': ('USGS',
                lambda: search_mod.source_usgs(keywords, location_city, salary_min, salary_max, limit, remote_only=remote_only)),
            'salesforce': ('Salesforce',
                lambda: search_mod.source_salesforce(keywords, limit)),
            'ramp': ('Ramp',
                lambda: search_mod.source_ramp(keywords, salary_min, salary_max, limit)),
        }

        for src_key in sources:
            if src_key not in source_map:
                continue
            src_name, fn = source_map[src_key]
            yield f'data: {json.dumps({"event": "source_start", "source": src_name})}\n\n'
            try:
                results = fn()
            except Exception as e:
                yield f'data: {json.dumps({"event": "source_error", "source": src_name, "error": str(e)})}\n\n'
                continue

            filtered = []
            for r in results:
                wtype = r.get('type', '')
                # Only filter by work type when the user has made an exclusive selection.
                # If multiple types are checked (or none), show everything.
                any_checked = remote or hybrid or onsite
                if any_checked:
                    allowed = set()
                    if remote: allowed.add('remote')
                    if hybrid: allowed.add('hybrid')
                    if onsite: allowed.add('onsite')
                    if wtype not in allowed:
                        continue

                # When onsite is checked, drop onsite results not in the Puget Sound area.
                if onsite and wtype == 'onsite':
                    loc_lower = r.get('location', '').lower() + ' '
                    if not any(t in loc_lower for t in PUGET_SOUND_TERMS):
                        continue

                key = (r.get('company', '').lower().strip(), r.get('title', '').lower().strip())
                if key in seen:
                    continue
                seen.add(key)
                filtered.append(r)

            yield f'data: {json.dumps({"event": "results", "source": src_name, "results": filtered})}\n\n'

        yield 'data: {"event":"done"}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/save', methods=['POST'])
def save():
    data = request.get_json()
    with get_db() as db:
        db.execute(
            """INSERT INTO job_applications
               (role, company, date_applied, status, source, location, salary_offer, notes, resume_variant)
               VALUES (?, ?, ?, 'applied', ?, ?, ?, ?, ?)""",
            (data.get('title'), data.get('company'), date.today().isoformat(),
             data.get('source'), data.get('location'),
             data.get('sal_lo'), data.get('url'), data.get('variant', ''))
        )
    return {'ok': True}


@app.route('/applications')
def applications():
    with get_db() as db:
        apps = db.execute(
            "SELECT * FROM job_applications ORDER BY date_applied DESC"
        ).fetchall()
    return render_template('applications.html', apps=apps, statuses=STATUSES,
                           variants=VARIANTS, status_css=STATUS_CSS)


@app.route('/applications/<int:app_id>/update', methods=['POST'])
def update_app(app_id):
    new_status  = request.form.get('status', '').strip()
    note        = request.form.get('note', '').strip()
    new_variant = request.form.get('variant', '').strip()

    with get_db() as db:
        row = db.execute(
            "SELECT status, resume_variant FROM job_applications WHERE id = ?", (app_id,)
        ).fetchone()
        if not row:
            return 'Not found', 404

        if new_status and new_status != row['status']:
            db.execute("UPDATE job_applications SET status = ? WHERE id = ?", (new_status, app_id))
            db.execute(
                """INSERT INTO application_log (application_id, field_changed, old_value, new_value, note)
                   VALUES (?, 'status', ?, ?, ?)""",
                (app_id, row['status'], new_status, note)
            )
        elif note:
            db.execute(
                "INSERT INTO application_log (application_id, note) VALUES (?, ?)",
                (app_id, note)
            )

        if new_variant and new_variant != (row['resume_variant'] or ''):
            db.execute("UPDATE job_applications SET resume_variant = ? WHERE id = ?", (new_variant, app_id))
            db.execute(
                """INSERT INTO application_log (application_id, field_changed, old_value, new_value, note)
                   VALUES (?, 'resume_variant', ?, ?, '')""",
                (app_id, row['resume_variant'], new_variant)
            )

    return redirect(url_for('applications'))


@app.route('/uncovered')
def uncovered():
    with get_db() as db:
        jobs = db.execute(
            "SELECT * FROM weekly_search_results WHERE hidden = 0 ORDER BY run_date DESC"
        ).fetchall()
    return render_template('uncovered.html', jobs=jobs)


@app.route('/uncovered/<int:job_id>/hide', methods=['POST'])
def hide_job(job_id):
    with get_db() as db:
        db.execute("UPDATE weekly_search_results SET hidden = 1 WHERE id = ?", (job_id,))
    return {'ok': True}


@app.route('/applications/<int:app_id>/log')
def app_log(app_id):
    with get_db() as db:
        logs = db.execute(
            "SELECT * FROM application_log WHERE application_id = ? ORDER BY timestamp",
            (app_id,)
        ).fetchall()
    return render_template('log_fragment.html', logs=logs)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5004, debug=True)
