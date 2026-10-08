"""Recall@k eval for rag_bullets.py against a hand-labeled (JD snippet -> expected
bullet) test set. Ground truth: each pair is a real JD requirement paired with
the bullet that resume was tailored to satisfy for that exact requirement.

Usage:
    python3.10 eval_rag.py
"""
import argparse
import json
from pathlib import Path

from sentence_transformers import SentenceTransformer

from rag_bullets import DATA_DIR, MODEL_NAME, get_embeddings, load_bullets, top_k

EVAL_FILE = DATA_DIR / "eval_set.json"


def run_eval(model_name=MODEL_NAME, ks=(3, 5, 10)):
    cases = json.loads(EVAL_FILE.read_text())
    bullets = load_bullets()
    model = SentenceTransformer(model_name)
    vecs = get_embeddings(bullets, model, model_name)

    max_k = max(ks)
    hit_texts = {}
    for case in cases:
        texts = [t for _, t, _ in top_k(case["query"], bullets, vecs, model, k=max_k)]
        hit_texts[case["query"]] = texts

    print(f"model: {model_name}")
    print(f"{len(cases)} eval cases\n")
    for k in ks:
        hits = sum(
            1 for c in cases if any(c["expected_substring"] in t for t in hit_texts[c["query"]][:k])
        )
        print(f"recall@{k}: {hits}/{len(cases)} = {hits / len(cases):.0%}")

    misses = [c for c in cases if not any(c["expected_substring"] in t for t in hit_texts[c["query"]][:5])]
    if misses:
        print("\nmissed at k=5:")
        for m in misses:
            print(" -", m["query"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL_NAME)
    args = ap.parse_args()
    run_eval(args.model)
