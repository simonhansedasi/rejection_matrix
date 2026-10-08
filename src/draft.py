#!/usr/bin/env python3
"""
Draft a first-pass application package for one weekly_search_results job:
a JD coverage table + disclosed-gaps list, a resume adapted from the general
template, and a finished cover letter draft (standing rule as of 2026-09-10 --
full prose in Simon's voice, not a scaffold, unless the listing itself states
an employer AI-use policy that calls for a scaffold instead). Everything
lands as data/*_DRAFT.{md,tex} -- never overwrites a non-DRAFT file. This is
a first pass for Simon to review and edit, not a submission-ready package;
the human review step downstream is unchanged.

Usage:
    python3 draft.py <weekly_search_results_id>
    python3 draft.py --dry-run <weekly_search_results_id>
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import rag_bullets

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
DB_PATH = os.path.join(DATA_DIR, 'applications.db')
RESUME_CONTEXT_PATH = os.path.expanduser('~/coding/contexts/RESUME_CONTEXT.md')
VOICE_PATH = os.path.expanduser('~/coding/simon_voice.md')
BASE_TEMPLATE_PATH = os.path.join(DATA_DIR, 'resume_ds_analyst.tex')
MODEL = "claude-sonnet-5-5"
REVIEW_MODEL = "claude-haiku-4-5-20251001"

# TARGET_PAGES matches BASE_TEMPLATE_PATH's own real, compiled page count --
# not the char-count guess this project used to rely on. That guess called
# resume_sentilink_ds.tex a "confirmed two-page document" (per RESUME_CONTEXT.md);
# it actually compiles to 3 pages. Now that LaTeX is installed locally, verify by
# compiling, don't estimate.
TARGET_PAGES = 1

REVIEW_SYSTEM_PROMPT = """You are doing a fresh, adversarial second pass on a job \
application package someone else drafted -- you were not involved in writing it and \
have no memory of that process. Two checks only.

1. CLAIM CHECK: read the resume and cover letter. Flag any claim that \
RESUME_CONTEXT.md does not corroborate, or that contradicts its VERIFIED CAPABILITY \
BOUNDARIES table (e.g. claiming a solver, Bayesian modeling, Tableau, or similar named \
gaps as real capability). Do not invent new gaps beyond what RESUME_CONTEXT.md already \
documents -- only flag actual contradictions or unsupported claims. If nothing is \
wrong, say so plainly in one line.

2. ATS KEYWORD COVERAGE: compare the job listing text to the resume + cover letter. \
List important keywords/phrases from the listing that appear in neither document. \
This is informational only -- never suggest keyword stuffing or padding to hit a match.

Output two short markdown sections, headed exactly:
**Claim check:**
**ATS keyword coverage:**
"""

SYSTEM_PROMPT = """You are drafting a FIRST-PASS job application package for Simon-Hans \
Edasi. This draft will be reviewed and edited by Simon before anything is submitted --
it is a starting point, not a finished package.

Ground truth: RESUME_CONTEXT.md (given below in full) is the ONLY source of truth for \
facts, dates, publication status, and verified capability boundaries. The base resume \
template (also given below) is a STRUCTURAL/style example only -- it is known to contain \
STALE facts (e.g. an outdated degree name, an outdated publication venue). Do NOT copy \
any fact from the template that RESUME_CONTEXT.md contradicts or that RESUME_CONTEXT.md \
does not corroborate. When RESUME_CONTEXT.md's "VERIFIED CAPABILITY BOUNDARIES" table \
lists a method as a gap, do not claim it -- name it as a disclosed gap instead. If the \
role covers any part of Aug 2025-Present, include RESUME_CONTEXT.md's "Independent \
Engineer & Researcher" work-experience entry if one exists there.

You will also be given a list of past resume bullets retrieved for relevance to this JD \
-- prefer these real, previously-used bullets over inventing new claims.

Never include a hidden/white-text prompt-injection block (this project used to do this; \
the policy is retired -- do not add one even if the base template once had one).

Never invent a LaTeX command for a special character (seen for real: \okina for the \
Hawaiian ʻokina broke a compile with no useful error). Write "Hawai'i" with a plain \
straight apostrophe, and in general prefer plain ASCII over any non-command Unicode \
character or a custom \command{} for it -- if unsure whether a character/command is \
safe in plain pdflatex with this template's exact preamble, don't use it.

When disclosing a capability gap, NEVER phrase it as "no X experience, but I did Y" \
(deficit-first). ALWAYS use "I have Y experience instead of X" (asset-first) -- lead with \
the real, substitutable experience as the headline claim, not as a consolation tacked \
onto a stated negative. The gap still gets named explicitly either way; only the sentence \
order and emphasis change. Example: not "I have not used Tableau or Power BI -- my \
dashboard experience is in Flask and Leaflet instead," but "I have dashboard experience \
in Flask and Leaflet instead of Tableau or Power BI."

Use the standard word for a real thing, not just the tool name. A recurring pattern found \
in this project's own automated ATS-keyword review: the resume demonstrates a skill but \
never uses the plain English term for it, so keyword-scanning software and skimming \
recruiters miss it. When it is true, say so in these exact words somewhere in the resume \
or cover letter (never as a bare keyword dump -- attached to the real project it belongs \
to): "machine learning" and "data science" (not just tool/library names), "data pipeline" \
or "ETL" (the fusion/ingestion work already described), "cloud" (Google Earth Engine, \
DigitalOcean, Hetzner are all real cloud infrastructure -- say so even though none of them \
is AWS/GCP/Azure), "analytics", "monitoring" or "observability" (ai_glue's actual purpose). \
This is a wording fix for true claims only -- never add a word for something not actually \
true, and never pad or repeat a term just to raise keyword density.

BULLET-WRITING RULES (2026-09-29, from an outside resume review, apply to every bullet): \
(1) LEAD WITH THE NUMBER. When a bullet has a real, verified metric, that metric opens the \
bullet ("$7.55M / 6,168-record invoice backlog quantified with a custom Python \
remittance-parsing toolkit", not "Built a toolkit... and quantified a $7.55M backlog"). \
Never invent a number to satisfy this; a bullet with no real metric leads with its \
strongest concrete fact instead. (2) VARY THE VERBS. No two bullets in the resume open with \
the same verb, and do not lean on "Built"/"Developed"/"Created" -- pick the verb that \
matches what was actually done (Architected, Shipped, Quantified, Engineered, Estimated, \
Instrumented, Automated, Trained, Supervised...). Number-led bullets still need a verb \
somewhere in the sentence; vary those too.

Output EXACTLY these four sections, each starting on its own line with the marker shown \
(all caps, three equals signs each side, nothing else on that line):

===COVERAGE_TABLE===
A JD line-by-line coverage table in markdown: every responsibility and qualification \
from the listing, whether it's real/partial/gap, and where it's addressed.

===RESUME_TEX===
A complete, compilable .tex resume file (same LaTeX conventions/preamble as the base \
template) adapted for this specific job.

STANDING PAGE-LAYOUT RULES (2026-09-10, apply to every draft, no exceptions): \
Header (name, then a one-line HEADLINE naming the target role in the JD's own title \
words plus 2-3 real specialties, e.g. "Data Scientist | Machine Learning, NLP, Geospatial \
Analytics" -- added 2026-09-29 after an outside review flagged its absence and it doubles \
as ATS title-match; then "Lynnwood, WA" leading the contact line, then email, website, and \
linkedin.com/in/simon-hans-edasi -- never drop LinkedIn) immediately followed by \
Technical Skills, Education, then Work Experience. The resume is ONE PAGE (changed \
2026-09-29 -- there is no Selected Projects section and no \\newpage anymore). Work \
Experience covers all four real jobs from RESUME_CONTEXT.md if the role covers that \
history; the three older jobs get at most ONE short bullet each. The Independent \
Engineer & Researcher entry carries the projects: 4-6 distilled bullets, one per \
project (pick the projects most relevant to this JD from RippleForge, ai_glue, \
Merascope, snotrac, statistical_uniqueness (all 2026, live) -- NEVER glacier_prethicktor here: it was grad-school work, not independent \
work; it lives as one italic "Graduate research" line under the MSc entry in Education), each ONE line where possible, two at most (the page is tight: verify it compiles to 1 page), naming the project \
and its strongest verified number/outcome -- distill, do not paste the old multi-bullet \
project blocks. A 3-LINE SUMMARY paragraph (unlabeled, no section heading, ~330 characters max so it \
renders as exactly 3 lines) sits between the header and Technical Skills -- added \
2026-09-29, reversing the old no-summary rule after an outside review flagged its absence. \
Same standards as bullets: only verified claims from RESUME_CONTEXT.md, lead with the \
strongest real facts (production systems, numbers), tailor the emphasis to the JD, and \
never claim a degree or title that isn't real (the MSc is Earth \\& Space Sciences). No separate Publications section -- fold a specific publication into a project \
bullet only if the JD specifically calls for research/publication history. Put \
\\needspace{Ncm} (roughly 1.2cm per rendered bullet line plus 1cm for the heading) \
before EVERY entry heading in both Work Experience and Selected Projects, and include \
\\usepackage{needspace} in the preamble -- this keeps an entry's heading and bullets on \
the same page instead of splitting across a page break (a real bug: an entry's heading \
landed at the bottom of a page with its bullets pushed to the next page). Compile and \
check the real page count and page boundaries before trusting the result -- do not \
assume the layout worked from the source alone.

===COVER_LETTER===
STANDING RULE (2026-09-10, updated same day): a finished, submission-ready cover \
letter, not an outline or scaffold -- full prose Simon can read, tweak, and send. \
As of the same day, this must be a complete, compilable .tex file, NOT markdown or \
plain text -- Simon wants a letter PDF alongside the resume PDF, not a plain-text \
file. Reuse the exact same preamble as the resume .tex (documentclass, geometry, \
enumitem/hyperref/titlesec/xcolor/needspace packages, \pagestyle{empty}) so the two \
documents look like one consistent package, and open with the same centered \
name/contact header (name, email, website, linkedin.com/in/simon-hans-edasi) as the \
resume. Below the header: a right- or left-aligned date line (use today's real date, \
not a placeholder), then the body as plain paragraphs (no \section, no bullets, no \
itemize -- a letter is prose) separated by \vspace{0.3cm} or a blank line, a \
salutation ("Dear Hiring Committee," unless a real named contact is known), then the \
letter body, then a closing ("Sincerely,\\ Simon-Hans Edasi"). Target ONE page -- \
this is compiled and checked the same way the resume is (compile_and_check_length), \
so keep it to what actually fits one page rather than relying on font tricks.

Write it in Simon's own voice (given above): professional register, no contractions, \
dry tone, semicolons preferred over em dashes, asset-first gap framing (never \
deficit-first), no overselling, no vague hedges. Ground every claim and figure in \
RESUME_CONTEXT.md -- the same sourcing discipline as the resume, just in letter form. \
Structure: an opening naming the specific role/division and the one-line core-method \
connection to the job; 2-4 body paragraphs each leading with the single strongest, \
most specific piece of real evidence for one part of the JD (a project or \
work-experience bullet with its real numbers, not a vague summary of it); one \
paragraph naming the real, disclosed gaps plainly and asset-first; a short \
logistics/closing paragraph (location, work type, availability) addressed honestly, \
never assumed. Do not pad it or perform enthusiasm -- state the fit and the gaps, \
then stop.

EXCEPTION -- fall back to a scaffold instead, and say so explicitly as the first line \
of this section: if the job listing text itself states an employer policy about AI \
use in applications (e.g. a requirement to disclose AI assistance, or write your own \
materials and only use AI to "refine" them -- Anthropic's own posting is the known \
precedent for this, see RESUME_CONTEXT.md/feedback_employer_ai_policy). Check the raw \
listing text for this before deciding; do not assume a policy exists or doesn't.

===GAPS===
A plain list of every real, disclosed gap between this candidate and the JD -- the \
things that must not be papered over.
===END===
"""


def _clean_component(s, max_len=60):
    s = re.sub(r"[^A-Za-z0-9]+", " ", s or "")
    s = re.sub(r"\s+", "_", s.strip())
    if len(s) > max_len:
        s = s[:max_len].rsplit("_", 1)[0]  # truncate at a word boundary, not mid-word
    return s.strip("_")


def _file_base(company, title):
    return f"Edasi_{_clean_component(company)}_{_clean_component(title)}"


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
    # compressed in the first place. See src/score.py for the same fix.
    kwargs = {
        "api_key": api_key,
        "default_headers": {"X-Aiglue-Project": "rejection_matrix", "Accept-Encoding": "identity"},
    }
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


def build_user_prompt(row, jd_text):
    resume_context = open(RESUME_CONTEXT_PATH).read() if os.path.exists(RESUME_CONTEXT_PATH) else \
        "(RESUME_CONTEXT.md not found -- proceed with maximum caution about unverified claims)"
    base_template = open(BASE_TEMPLATE_PATH).read() if os.path.exists(BASE_TEMPLATE_PATH) else ""
    voice = open(VOICE_PATH).read() if os.path.exists(VOICE_PATH) else \
        "(simon_voice.md not found -- default to a plain, dry, professional register: no contractions, semicolons over em dashes, no overselling.)"

    bullets = rag_bullets.load_bullets()
    if bullets:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(rag_bullets.MODEL_NAME)
        vecs = rag_bullets.get_embeddings(bullets, model)
        top = rag_bullets.top_k(jd_text, bullets, vecs, model, k=8)
        bullets_block = "\n".join(f"- ({company}) {text}" for company, text, _ in top)
    else:
        bullets_block = "(no past bullets found)"

    return f"""JOB LISTING
Title: {row['title']}
Company: {row['company']}
Source: {row['source']}
Raw listing data: {jd_text}

RETRIEVED PAST BULLETS RELEVANT TO THIS JD (prefer these over inventing new claims)
{bullets_block}

RESUME_CONTEXT.md (ground truth)
{resume_context}

BASE RESUME TEMPLATE (structure/style only -- known to contain stale facts)
{base_template}

SIMON'S WRITING VOICE (match this for the cover letter -- professional-register rules apply: no contractions, dry tone, semicolons over em dashes, no overselling, no vague hedges)
{voice}
"""


SECTION_ORDER = ["COVERAGE_TABLE", "RESUME_TEX", "COVER_LETTER", "GAPS"]


def parse_sections(text):
    parts = {}
    pattern = "|".join(f"==={name}===" for name in SECTION_ORDER) + "|===END==="
    chunks = re.split(f"({pattern})", text)
    current = None
    for chunk in chunks:
        marker = chunk.strip().strip("=")
        if marker in SECTION_ORDER:
            current = marker
        elif marker == "END":
            current = None
        elif current:
            parts[current] = parts.get(current, "") + chunk
    parts = {k: v.strip() for k, v in parts.items()}
    for k, v in parts.items():
        # The model occasionally wraps a section in a stray ```lang / ``` fence
        # (seen for real: broke the .tex compile with no useful error otherwise).
        parts[k] = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", v).strip()
    return parts


def _compile_once(tex_path):
    """One latexmk attempt. Returns (stdout_or_None, error_message_or_None)."""
    tex_dir = os.path.dirname(tex_path)
    tex_name = os.path.basename(tex_path)
    try:
        result = subprocess.run(
            # -g forces a real rebuild -- without it, latexmk's mtime-based cache
            # can decide "nothing to do" and skip printing the page-count line
            # (seen for real: a leftover .pdf from a prior manual compile caused
            # a false COMPILE FAILED verdict here).
            ["latexmk", "-pdf", "-g", "-interaction=nonstopmode", "-halt-on-error", tex_name],
            cwd=tex_dir, capture_output=True, text=True, timeout=90,
        )
        return result.stdout, None
    except subprocess.TimeoutExpired:
        return None, "latexmk timed out after 90s -- possible LaTeX error causing an infinite loop"
    finally:
        subprocess.run(["latexmk", "-c", tex_name], cwd=tex_dir,
                        capture_output=True, timeout=30)  # drop .aux/.log/.out, keep the .pdf


def compile_and_check_length(tex_path, target_pages=TARGET_PAGES):
    """Compile a just-written .tex file with latexmk and report its real page
    count against target_pages (1 for both the resume and the cover letter --
    see draft_package). Never raises -- any failure (latexmk missing, a
    LaTeX error, a timeout) comes back as a message instead of blocking the rest
    of the review. Returns (message, pdf_path_or_None).

    Was misdiagnosed once as an "environmental flake" and given a retry wrapper
    (removed) -- the real bug: pdflatex's own log wraps long lines at ~79 chars,
    and "Output written on <name>.pdf (N pages, ...)" wraps mid-parenthetical
    for any filename long enough to push "(N" onto one line and "pages," onto
    the next (real case: the Opendoor drafts' long base filenames, every time --
    not random). The old regex required a literal space between the digit and
    "pages", which a newline doesn't satisfy. \\s+ (matches newlines too) fixes
    it for real, so no retry is needed -- verify with `\\((\\d+)\\s+pages?,` should
    that ever regress."""
    if shutil.which("latexmk") is None:
        return "latexmk not installed on this machine -- skipping compiled length check", None

    tex_dir = os.path.dirname(tex_path)
    tex_name = os.path.basename(tex_path)
    pdf_path = os.path.join(tex_dir, os.path.splitext(tex_name)[0] + ".pdf")

    stdout, timeout_msg = _compile_once(tex_path)
    if timeout_msg:
        return timeout_msg, None

    # pdflatex hard-wraps at 79 chars, even mid-word ("(1 pa\nge,"), so join lines first
    match = re.search(r"\((\d+)\s*pages?,", stdout.replace("\n", ""))
    if not match:
        tail = "\n".join(stdout.splitlines()[-15:])
        return f"COMPILE FAILED -- {tex_name} does not compile. latexmk tail:\n{tail}", None

    pages = int(match.group(1))
    if pages == target_pages:
        verdict = "OK"
    elif pages < target_pages:
        verdict = "too short -- check whether content is thin, or this is intentionally a one-pager"
    else:
        verdict = f"over the {target_pages}-page target -- needs a structural cut, not word-level trimming"
    return f"compiled to {pages} page(s), target {target_pages} -- {verdict}", pdf_path


def review_package(client, row, jd_text, sections, resume_context):
    user_prompt = f"""JOB LISTING
Title: {row['title']}
Company: {row['company']}
Raw listing data: {jd_text}

RESUME_CONTEXT.md (ground truth)
{resume_context}

DRAFTED RESUME
{sections.get('RESUME_TEX', '(missing)')}

DRAFTED COVER LETTER
{sections.get('COVER_LETTER', '(missing)')}
"""
    resp = client.messages.create(
        model=REVIEW_MODEL,
        max_tokens=1500,
        system=REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text_blocks = [b.text for b in resp.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError(f"no text block in review response (blocks: {[b.type for b in resp.content]})")
    return "".join(text_blocks).strip()


def draft_package(weekly_search_results_id, dry_run=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM weekly_search_results WHERE id = ?", (weekly_search_results_id,)
        ).fetchone()
        if not row:
            print(f"no weekly_search_results row with id {weekly_search_results_id}")
            return None

        jd_text = row['raw_json'] or f"{row['title']} at {row['company']}"
        prompt = build_user_prompt(row, jd_text)

        if dry_run:
            print("--- system prompt ---")
            print(SYSTEM_PROMPT)
            print("--- user prompt (truncated to 3000 chars) ---")
            print(prompt[:3000])
            return None

        client = _client()
        # Streamed, not a single blocking create() call -- the ai_glue proxy has its
        # own ~120s upstream read timeout, and a full package (resume + cover letter
        # + coverage table, plus thinking tokens) can take longer than that to
        # generate. Streaming avoids the proxy ever waiting on one long blocking read.
        # max_tokens raised 16000 -> 32000 (2026-09-10): a full finished cover letter
        # (standing rule as of the same day, not a scaffold) pushed total output past
        # 16000 and got silently truncated before the model ever reached the
        # ===COVER_LETTER=== marker -- parse_sections() then wrote "(missing)" with
        # no error. Sonnet 5 supports up to 128K output tokens when streaming (already
        # the case here), so there's no timeout tradeoff to raising this further if
        # 32000 isn't enough.
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for _ in stream.text_stream:
                pass
            resp = stream.get_final_message()
        if resp.stop_reason == "max_tokens":
            print("WARNING: response was truncated at max_tokens -- output is incomplete")
        text_blocks = [b.text for b in resp.content if b.type == "text"]
        if not text_blocks:
            raise RuntimeError(f"no text block in response (blocks: {[b.type for b in resp.content]})")
        sections = parse_sections("".join(text_blocks))

        base = _file_base(row['company'], row['title'])
        cover_content = sections.get("COVER_LETTER", "(missing)")
        # The model falls back to a plain scaffold (not .tex) when a JD states an
        # employer AI-use policy -- detect that instead of assuming .tex always
        # comes back, or compiling non-LaTeX content as if it were a resume.
        cover_is_tex = "\\documentclass" in cover_content
        cover_ext = "tex" if cover_is_tex else "md"
        paths = {
            "role_descriptions": os.path.join(DATA_DIR, f"{base}_notes_DRAFT.md"),
            "resume": os.path.join(DATA_DIR, f"{base}_resume_DRAFT.tex"),
            "cover": os.path.join(DATA_DIR, f"{base}_coverletter_DRAFT.{cover_ext}"),
        }

        role_desc = sections.get("COVERAGE_TABLE", "(missing)") + "\n\n## Disclosed gaps\n\n" + \
            sections.get("GAPS", "(missing)")
        with open(paths["role_descriptions"], "w") as f:
            f.write(role_desc)
        with open(paths["resume"], "w") as f:
            f.write(sections.get("RESUME_TEX", "% (missing -- model did not return this section)"))
        with open(paths["cover"], "w") as f:
            f.write(cover_content)

        conn.execute("UPDATE weekly_search_results SET drafted = 1 WHERE id = ?",
                     (weekly_search_results_id,))
        conn.commit()

        length_msg, pdf_path = compile_and_check_length(paths["resume"])
        review_lines = [
            "\n\n## Automated Review (fresh pass, not from the drafting model)\n",
            f"**Length:** {length_msg}\n",
        ]
        if cover_is_tex:
            cover_length_msg, cover_pdf_path = compile_and_check_length(paths["cover"], target_pages=1)
            review_lines.append(f"**Cover letter length:** {cover_length_msg}\n")
            if cover_pdf_path:
                paths["cover_pdf"] = cover_pdf_path
        try:
            resume_context = open(RESUME_CONTEXT_PATH).read() if os.path.exists(RESUME_CONTEXT_PATH) else \
                "(RESUME_CONTEXT.md not found)"
            review_lines.append("\n" + review_package(client, row, jd_text, sections, resume_context))
        except Exception as e:
            review_lines.append(f"\n(automated review failed: {e})")
        with open(paths["role_descriptions"], "a") as f:
            f.writelines(review_lines)
        if pdf_path:
            paths["resume_pdf"] = pdf_path

        for label, path in paths.items():
            print(f"wrote {path}")
        return paths
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _load_env()
    draft_package(args.id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
