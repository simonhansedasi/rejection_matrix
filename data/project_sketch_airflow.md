# Project Sketch — Airflow rejection_matrix DAG

**Goal:** Build an Airflow DAG that orchestrates the rejection_matrix weekly job search workflow. Airflow is explicitly called out as a "strong plus" in the Salesforce JD; this is the fastest path to a citable project.

**Time estimate:** 2-4 hours

---

## Setup

```bash
pip install apache-airflow

# Initialize the metadata DB and create an admin user
export AIRFLOW_HOME=~/airflow
airflow db init
airflow users create --username admin --password admin \
    --firstname Simon-Hans --lastname Edasi --role Admin --email simonhansedasi@gmail.com

# Two terminals:
airflow webserver --port 8081   # UI at localhost:8081
airflow scheduler
```

Use port 8081 to avoid colliding with anything already on 8080.

---

## DAG structure

Five tasks, linear dependency chain:

```
search_jobs >> deduplicate >> filter_results >> write_to_db >> notify
```

File: `~/airflow/dags/rejection_matrix_weekly.py`

---

## Step 1 — search_jobs

Call `search.py` programmatically. Import the existing source functions rather than shelling out.

```python
import sys
sys.path.insert(0, "/home/simonhans/coding/rejection_matrix/src")
from search import search_remotive, search_wwr, search_the_muse

def _search_jobs(**context):
    results = []
    for fn in [search_remotive, search_wwr, search_the_muse]:
        try:
            results.extend(fn(keyword="data scientist"))
        except Exception:
            pass  # one source failing should not abort the run
    context["ti"].xcom_push(key="raw_results", value=results)
```

Skip JSearch/USAJOBS here to avoid burning the 200 req/month free tier on automated runs.

---

## Step 2 — deduplicate

Remove listings already in `applications.db` by matching on title + company.

```python
import sqlite3

def _deduplicate(**context):
    raw = context["ti"].xcom_pull(key="raw_results", task_ids="search_jobs")
    conn = sqlite3.connect("/home/simonhans/coding/rejection_matrix/data/applications.db")
    existing = {(r[0], r[1]) for r in conn.execute(
        "SELECT title, company FROM job_applications"
    )}
    conn.close()
    fresh = [j for j in raw if (j.get("title"), j.get("company")) not in existing]
    context["ti"].xcom_push(key="fresh_results", value=fresh)
```

---

## Step 3 — filter_results

Keyword filter: keep jobs whose title contains at least one target term.

```python
KEYWORDS = {"data scientist", "data science", "product analytics",
            "quantitative", "machine learning", "analyst"}

def _filter_results(**context):
    fresh = context["ti"].xcom_pull(key="fresh_results", task_ids="deduplicate")
    filtered = [
        j for j in fresh
        if any(k in (j.get("title") or "").lower() for k in KEYWORDS)
    ]
    context["ti"].xcom_push(key="filtered_results", value=filtered)
```

---

## Step 4 — write_to_db

Write a weekly results summary to a new table `weekly_search_results` (separate from tracked applications).

```python
import sqlite3, json
from datetime import datetime

def _write_to_db(**context):
    results = context["ti"].xcom_pull(key="filtered_results", task_ids="filter_results")
    conn = sqlite3.connect("/home/simonhans/coding/rejection_matrix/data/applications.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT,
            title TEXT,
            company TEXT,
            url TEXT,
            source TEXT,
            raw_json TEXT
        )
    """)
    run_date = datetime.utcnow().isoformat()
    conn.executemany(
        "INSERT INTO weekly_search_results (run_date, title, company, url, source, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(run_date, j.get("title"), j.get("company"), j.get("url"),
          j.get("source"), json.dumps(j)) for j in results]
    )
    conn.commit()
    conn.close()
    context["ti"].xcom_push(key="result_count", value=len(results))
```

---

## Step 5 — notify

Send a push via ntfy.sh (same mechanism used in mauna_loa).

```python
import requests

def _notify(**context):
    count = context["ti"].xcom_pull(key="result_count", task_ids="write_to_db")
    requests.post(
        "https://ntfy.sh/simonhans-job-alerts",  # pick any topic name
        data=f"{count} new job listings found — check rejection_matrix",
        headers={"Title": "Weekly Job Search"}
    )
```

---

## Step 6 — wire it together

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {"retries": 1, "retry_delay": timedelta(minutes=5)}

with DAG(
    dag_id="rejection_matrix_weekly",
    start_date=datetime(2026, 5, 18),
    schedule_interval="0 8 * * 1",  # Monday 8am
    default_args=default_args,
    catchup=False,
) as dag:

    t1 = PythonOperator(task_id="search_jobs",      python_callable=_search_jobs)
    t2 = PythonOperator(task_id="deduplicate",      python_callable=_deduplicate)
    t3 = PythonOperator(task_id="filter_results",   python_callable=_filter_results)
    t4 = PythonOperator(task_id="write_to_db",      python_callable=_write_to_db)
    t5 = PythonOperator(task_id="notify",           python_callable=_notify)

    t1 >> t2 >> t3 >> t4 >> t5
```

---

## Verify it works

Trigger manually from the UI or CLI before waiting for Monday:

```bash
airflow dags trigger rejection_matrix_weekly
airflow dags test rejection_matrix_weekly 2026-05-18
```

Watch task states in the UI at `localhost:8081`.

---

## What to add to the resume

```
Built an Apache Airflow DAG orchestrating a weekly job search pipeline across 3 sources;
5-task chain (search → deduplicate → filter → persist → notify) with XCom state passing,
retry logic, and ntfy.sh push notifications on completion.
```

---

## Files to create

- `~/airflow/dags/rejection_matrix_weekly.py` — the DAG
- Optionally move task functions into `rejection_matrix/src/dag_tasks.py` and import them, keeping the DAG file thin
