# Job Search Workflow

## Resume variants (build these first)

Keep 2–3 pre-built resumes in Overleaf. Tailoring is then swapping bullets at the margins, not rebuilding from scratch.

| Variant | Leads with |
|---|---|
| `research` | glacier_prethicktor (published paper) + GrapeExpectations (R²=0.967, WSU) |
| `geo` | GrapeExpectations geospatial pipeline + glacier global coverage |
| `simulation` | sf_majick (185-param calibration, OLS intervention) + gov_inertia + encounterMCMC |

---

## Weekly cycle

### 1. Search
Run rejection_matrix against your target roles. Good keywords: `data scientist`, `research scientist`, `geospatial`, `remote sensing`, `quantitative analyst`, `simulation engineer`.

```bash
python3 src/search.py "research scientist" --remote
python3 src/search.py "geospatial data scientist" --remote
python3 src/search.py "quantitative analyst" --remote --salary-min 90000
```

### 2. Triage
Quick pass — apply / skip / maybe. Apply if the JD asks for things you actually did on 60%+ overlap. Don't overthink it.

### 3. Tailor (per application, ~15 min)
1. Paste the JD to Claude with: *"Tailor my resume for this JD"*
2. Claude pulls from `RESUME_CONTEXT.md`, reorders bullets to match their language
3. Paste updated bullets into the appropriate Overleaf variant
4. Export PDF

### 4. Apply + log
Submit. Log immediately.

```bash
python3 src/rm.py add "Company" "Role" --source LinkedIn --location Remote --variant research
```

If you saved directly from search with `--save`, update the variant:

```bash
python3 src/rm.py update <id> "applied, used research resume" --variant research
```

### 5. Track
No response in 2 weeks → mark ghosted and move on.

```bash
python3 src/rm.py update <id> "no response" --status ghosted
python3 src/rm.py list --status applied   # what's still pending
python3 src/rm.py list --status interviewing
```

---

## Target roles

| Role | Why |
|---|---|
| Research Scientist / Applied ML | Published JoG paper + WSU collaboration |
| Geospatial / Remote Sensing Data Scientist | GrapeExpectations + glacier pipelines; specialized skill most candidates lack |
| Precision agriculture / agtech | GrapeExpectations directly applicable |
| Environmental / climate analytics | Glacier sea-level work + vineyard microclimate |
| Quantitative Analyst / Simulation Engineer | sf_majick 185-param calibration, Monte Carlo work |

Avoid: pure software engineering, narrow domain specialist roles — the edge is methods and research depth, not systems architecture.

---

## Useful queries

```bash
# See everything active
python3 src/rm.py list --status applied
python3 src/rm.py list --status interviewing

# How much time have I spent on this?
python3 src/rm.py activity --days 30

# Pull the apply URL for an application
sqlite3 data/applications.db "SELECT notes FROM job_applications WHERE id = <id>;"

# See full audit trail for an application
sqlite3 data/applications.db "SELECT timestamp, field_changed, old_value, new_value, note FROM application_log WHERE application_id = <id>;"
```
