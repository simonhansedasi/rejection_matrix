"""Retrieve past resume bullets relevant to a new job description.

Corpus = every \\item line in data/resume_*.tex, tagged with the company
slug from the filename. Embeddings cached to data/.rag_cache/ so re-runs
don't re-embed 300 bullets every time.

Usage:
    python3.10 rag_bullets.py "paste JD text here" [--k 5]
    python3.10 rag_bullets.py --selftest
"""
import argparse
import glob
import json
import re
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_DIR = DATA_DIR / ".rag_cache"
MODEL_NAME = "all-MiniLM-L6-v2"


def load_bullets():
    bullets = []
    for path in sorted(DATA_DIR.glob("resume_*.tex")):
        company = path.stem.removeprefix("resume_")
        text = path.read_text()
        for item in re.findall(r"\\item (.+)", text):
            clean = re.sub(r"\\textbf\{([^}]*)\}", r"\1", item)
            clean = re.sub(r"\\[a-zA-Z]+(\{[^}]*\})?", "", clean).strip()
            if clean:
                bullets.append({"company": company, "text": clean})
    return bullets


def get_embeddings(bullets, model, model_name=MODEL_NAME):
    CACHE_DIR.mkdir(exist_ok=True)
    tag = model_name.replace("/", "_")
    cache_file = CACHE_DIR / f"bullets_{tag}.json"
    vec_file = CACHE_DIR / f"bullets_{tag}.npy"
    texts = [b["text"] for b in bullets]

    if cache_file.exists() and vec_file.exists():
        cached = json.loads(cache_file.read_text())
        if cached == texts:
            return np.load(vec_file)

    vecs = model.encode(texts, normalize_embeddings=True)
    np.save(vec_file, vecs)
    cache_file.write_text(json.dumps(texts))
    return vecs


def top_k(query, bullets, vecs, model, k=5):
    qvec = model.encode([query], normalize_embeddings=True)[0]
    scores = vecs @ qvec
    idx = np.argsort(-scores)[:k]
    return [(bullets[i]["company"], bullets[i]["text"], float(scores[i])) for i in idx]


def selftest(model_name=MODEL_NAME):
    bullets = load_bullets()
    assert len(bullets) > 100, f"expected >100 bullets, got {len(bullets)}"
    model = SentenceTransformer(model_name)
    vecs = get_embeddings(bullets, model, model_name)
    assert vecs.shape[0] == len(bullets)

    probe = bullets[0]["text"]
    results = top_k(probe, bullets, vecs, model, k=1)
    assert results[0][1] == probe, "query for a bullet's own text should retrieve itself top-1"
    print(f"selftest ok: {len(bullets)} bullets, embeddings shape {vecs.shape}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="job description text to match against")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--model", default=MODEL_NAME)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest or not args.query:
        selftest(args.model)
        return

    bullets = load_bullets()
    model = SentenceTransformer(args.model)
    vecs = get_embeddings(bullets, model, args.model)
    for company, text, score in top_k(args.query, bullets, vecs, model, args.k):
        print(f"[{score:.3f}] ({company}) {text}")


if __name__ == "__main__":
    main()
