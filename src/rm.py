#!/usr/bin/env python3
"""rejection_matrix — job application tracker CLI.

Usage:
  rm.py add <company> <role> [--date DATE] [--source SOURCE] [--location LOC] [--salary SALARY] [--industry INDUSTRY] [--notes NOTES]
  rm.py update <id> <note> [--status STATUS]
  rm.py list [--status STATUS]
  rm.py activity [--days N]
"""

import argparse
import sqlite3
import os
from datetime import date, datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'applications.db')
TRACKER_DB = os.path.join(
    os.path.dirname(__file__), '..', '..', 'dada_science', 'personal_tracker', 'personal.db'
)

STATUSES   = ['applied', 'interviewing', 'rejected', 'ghosted', 'scam', 'dead', 'offer', 'onboarding']
DOC_TYPES  = ['resume', 'cover_letter', 'portfolio', 'writing_sample', 'other']

STATUS_COLORS = {
    'applied':      '\033[36m',   # cyan
    'interviewing': '\033[32m',   # green
    'offer':        '\033[1;32m', # bold green
    'rejected':     '\033[31m',   # red
    'ghosted':      '\033[33m',   # yellow
    'scam':         '\033[35m',   # magenta
    'dead':         '\033[90m',   # dark grey
}
RESET = '\033[0m'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def color_status(status):
    c = STATUS_COLORS.get(status, '')
    return f"{c}{status}{RESET}" if c else status


# ── add ────────────────────────────────────────────────────────────────────────

def cmd_add(args):
    applied = args.date or date.today().isoformat()
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO job_applications
               (role, company, date_applied, status, source, location, salary_offer, industry, notes, resume_variant)
               VALUES (?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?)""",
            (args.role, args.company, applied, args.source, args.location, args.salary, args.industry, args.notes, args.variant),
        )
        row_id = cur.lastrowid
    print(f"Added #{row_id}: {args.role} @ {args.company} ({applied})")


# ── update ─────────────────────────────────────────────────────────────────────

def cmd_update(args):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM job_applications WHERE id = ?", (args.id,)
        ).fetchone()
        if not row:
            print(f"No application with id {args.id}")
            return

        if args.status:
            old_status = row['status']
            db.execute(
                "UPDATE job_applications SET status = ? WHERE id = ?",
                (args.status, args.id),
            )
            db.execute(
                """INSERT INTO application_log
                   (application_id, field_changed, old_value, new_value, note)
                   VALUES (?, 'status', ?, ?, ?)""",
                (args.id, old_status, args.status, args.note),
            )
            print(f"#{args.id} {row['company']} — status: {old_status} → {args.status}")

        if args.variant:
            old_variant = row['resume_variant'] or ''
            db.execute(
                "UPDATE job_applications SET resume_variant = ? WHERE id = ?",
                (args.variant, args.id),
            )
            db.execute(
                """INSERT INTO application_log
                   (application_id, field_changed, old_value, new_value, note)
                   VALUES (?, 'resume_variant', ?, ?, ?)""",
                (args.id, old_variant, args.variant, args.note),
            )
            print(f"#{args.id} {row['company']} — resume_variant: {old_variant or '(none)'} → {args.variant}")

        if not args.status and not args.variant:
            db.execute(
                """INSERT INTO application_log
                   (application_id, note)
                   VALUES (?, ?)""",
                (args.id, args.note),
            )
            print(f"#{args.id} {row['company']} — logged: {args.note}")


# ── list ───────────────────────────────────────────────────────────────────────

def cmd_list(args):
    with get_db() as db:
        if args.status:
            rows = db.execute(
                "SELECT * FROM job_applications WHERE status = ? ORDER BY date_applied DESC",
                (args.status,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM job_applications ORDER BY date_applied DESC"
            ).fetchall()

    if not rows:
        print("No applications found.")
        return

    # column widths
    id_w      = max(len(str(r['id'])) for r in rows)
    comp_w    = max(len(r['company'] or '') for r in rows)
    role_w    = max(len(r['role'] or '') for r in rows)
    date_w    = 10
    stat_w    = max(len(r['status'] or '') for r in rows)
    variant_w = max(max(len(r['resume_variant'] or '') for r in rows), len('Variant'))

    def fmt_salary(val):
        return f"${val:,.0f}" if val is not None else ''

    sal_w = max(max(len(fmt_salary(r['salary_offer'])) for r in rows), len('Salary'))

    header = (
        f"{'#':<{id_w}}  "
        f"{'Company':<{comp_w}}  "
        f"{'Role':<{role_w}}  "
        f"{'Applied':<{date_w}}  "
        f"{'Salary':<{sal_w}}  "
        f"{'Variant':<{variant_w}}  "
        f"Status"
    )
    print(header)
    print('─' * len(header))

    for r in rows:
        cstatus = color_status(r['status'] or '')
        print(
            f"{str(r['id']):<{id_w}}  "
            f"{(r['company'] or ''):<{comp_w}}  "
            f"{(r['role'] or ''):<{role_w}}  "
            f"{(r['date_applied'] or ''):<{date_w}}  "
            f"{fmt_salary(r['salary_offer']):<{sal_w}}  "
            f"{(r['resume_variant'] or ''):<{variant_w}}  "
            f"{cstatus}"
        )


# ── docs ────────────────────────────────────────────────────────────────────────

def cmd_docs(args):
    with get_db() as db:
        if args.docs_cmd == 'add':
            row = db.execute(
                "SELECT company, role FROM job_applications WHERE id = ?", (args.app_id,)
            ).fetchone()
            if not row:
                print(f"No application with id {args.app_id}")
                return
            cur = db.execute(
                """INSERT INTO application_documents (application_id, type, path, label)
                   VALUES (?, ?, ?, ?)""",
                (args.app_id, args.type, args.path, args.label),
            )
            print(f"Doc #{cur.lastrowid} attached to #{args.app_id} ({row['company']} — {row['role']})")

        elif args.docs_cmd == 'list':
            row = db.execute(
                "SELECT company, role FROM job_applications WHERE id = ?", (args.app_id,)
            ).fetchone()
            if not row:
                print(f"No application with id {args.app_id}")
                return
            docs = db.execute(
                "SELECT * FROM application_documents WHERE application_id = ? ORDER BY added_at",
                (args.app_id,),
            ).fetchall()
            print(f"Docs for #{args.app_id} {row['company']} — {row['role']}")
            if not docs:
                print("  (none)")
                return
            for d in docs:
                label = f"  [{d['label']}]" if d['label'] else ''
                print(f"  #{d['id']}  {d['type'] or 'other':<14}  {d['path']}{label}")

        elif args.docs_cmd == 'rm':
            doc = db.execute(
                "SELECT * FROM application_documents WHERE id = ?", (args.doc_id,)
            ).fetchone()
            if not doc:
                print(f"No document with id {args.doc_id}")
                return
            db.execute("DELETE FROM application_documents WHERE id = ?", (args.doc_id,))
            print(f"Removed doc #{args.doc_id}  ({doc['path']})")


# ── activity ───────────────────────────────────────────────────────────────────

def cmd_activity(args):
    days = args.days or 14
    if not os.path.exists(TRACKER_DB):
        print(f"Tracker DB not found at {TRACKER_DB}")
        return

    conn = sqlite3.connect(TRACKER_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT date, category, description, start_time, end_time,
                  ROUND(
                    (strftime('%s', end_time) - strftime('%s', start_time)) / 60.0
                  ) AS duration_min
           FROM time_blocks
           WHERE category IN ('Job Search', 'LinkedIn')
             AND date >= date('now', ?)
             AND end_time IS NOT NULL
           ORDER BY date DESC, start_time""",
        (f'-{days} days',),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No job-search activity in the last {days} days.")
        return

    total_min = sum(r['duration_min'] or 0 for r in rows)
    print(f"Job search activity — last {days} days ({total_min:.0f} min total)\n")

    cur_date = None
    day_min = 0
    for r in rows:
        if r['date'] != cur_date:
            if cur_date:
                print(f"  → {day_min:.0f} min\n")
            cur_date = r['date']
            day_min = 0
            print(f"{r['date']}")
        dur = r['duration_min'] or 0
        day_min += dur
        desc = r['description'] or ''
        print(f"  {r['start_time'][11:16]}  [{r['category']}]  {desc}  ({dur:.0f}m)")
    if cur_date:
        print(f"  → {day_min:.0f} min")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog='rm.py', description='Job application tracker')
    sub = parser.add_subparsers(dest='cmd', required=True)

    # add
    p_add = sub.add_parser('add', help='Add a new application')
    p_add.add_argument('company')
    p_add.add_argument('role')
    p_add.add_argument('--date',     help='Applied date (YYYY-MM-DD), default today')
    p_add.add_argument('--source',   help='Where you found it (LinkedIn, etc.)')
    p_add.add_argument('--location', help='Location / remote')
    p_add.add_argument('--salary',   type=float, help='Salary offer')
    p_add.add_argument('--industry', help='Company industry (e.g. Fintech, Healthcare)')
    p_add.add_argument('--notes',    help='Free-text notes')
    p_add.add_argument('--variant',  help='Resume variant used (e.g. geo, research, simulation)')

    # update
    p_upd = sub.add_parser('update', help='Log an update for an application')
    p_upd.add_argument('id',   type=int)
    p_upd.add_argument('note', help='What happened')
    p_upd.add_argument('--status',  choices=STATUSES, help='New status')
    p_upd.add_argument('--variant', help='Update resume variant')

    # list
    p_lst = sub.add_parser('list', help='List applications')
    p_lst.add_argument('--status', choices=STATUSES, help='Filter by status')

    # docs
    p_docs = sub.add_parser('docs', help='Manage documents attached to an application')
    docs_sub = p_docs.add_subparsers(dest='docs_cmd', required=True)

    p_docs_add = docs_sub.add_parser('add', help='Attach a document')
    p_docs_add.add_argument('app_id', type=int, help='Application id')
    p_docs_add.add_argument('path', help='File path to the document')
    p_docs_add.add_argument('--type', choices=DOC_TYPES, default='other', help='Document type')
    p_docs_add.add_argument('--label', help='Optional label (e.g. "resume_v2", "tailored fintech")')

    p_docs_list = docs_sub.add_parser('list', help='List documents for an application')
    p_docs_list.add_argument('app_id', type=int, help='Application id')

    p_docs_rm = docs_sub.add_parser('rm', help='Remove a document entry')
    p_docs_rm.add_argument('doc_id', type=int, help='Document id')

    # activity
    p_act = sub.add_parser('activity', help='Show job-search activity from personal tracker')
    p_act.add_argument('--days', type=int, default=14, help='Look-back window (default 14)')

    args = parser.parse_args()
    {'add': cmd_add, 'update': cmd_update, 'list': cmd_list, 'docs': cmd_docs, 'activity': cmd_activity}[args.cmd](args)


if __name__ == '__main__':
    main()
