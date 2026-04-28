"""
fix_skills.py — One-time script to reconstruct broken skill names.

Root cause: frontend regex /[,\\n]/ split skill names on the letter 'n'.
  "Python"                        → ["Pytho"]
  "Machine Learning Fundamentals" → ["Machi", "e Lear", "g Fu", "dame", "tals"]
  "Linear Algebra"                → ["Li", "ear Algebra"]
  "PyTorch or TensorFlow"         → ["PyTorch or Te", "sorFlow"]

Reconstruction steps:
  1. Join all fragments back with 'n' (undo the bad split).
  2. Insert spaces at camelCase/PascalCase boundaries (e.g. "PythonMachine" → "Python Machine").
  3. Remove isolated artefact 'n' tokens left after step 1.
  4. Use dslim/bert-base-NER on the cleaned text to find entity spans.
  5. Fall back to BERT WordPiece word-boundary splitting for terms NER misses.
  6. Wipe broken rows, insert clean ones, commit.

Usage:
    cd recruitment-system-back
    python fix_skills.py [--dry-run]
"""
from __future__ import annotations

import re
import sys
import os
from collections import defaultdict

# Force HuggingFace to download models to E: drive
os.environ["HF_HOME"] = r"E:\huggingface_cache"
os.environ["TRANSFORMERS_CACHE"] = r"E:\huggingface_cache\hub"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from database.connection import SessionLocal
from models.db.requisition_required_skill import RequisitionRequiredSkill


# ── Text cleaning helpers ─────────────────────────────────────────────────────

def _insert_camel_spaces(text: str) -> str:
    """
    Insert a space before every uppercase letter that follows a lowercase letter.
    "PythonMachine" → "Python Machine"
    "FundamentalsLinear" → "Fundamentals Linear"
    """
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', text)


def _remove_artefact_n(text: str) -> str:
    """
    Remove isolated 'n' tokens that are split artefacts.
    "Calculus n Git" → "Calculus Git"
    Also fix words that picked up a trailing 'n' they shouldn't have
    (e.g. "Algebran" → "Algebra") by checking the BERT vocab.
    """
    # Remove standalone ' n ' tokens
    text = re.sub(r'\s+n\s+', ' ', text)
    text = re.sub(r'\s+n$', '', text)
    text = re.sub(r'^n\s+', '', text)
    return text.strip()


def _clean_joined(fragments: list[str]) -> str:
    """Full cleaning pipeline on the joined fragment string."""
    joined = 'n'.join(fragments)
    joined = _insert_camel_spaces(joined)
    joined = _remove_artefact_n(joined)
    # Collapse multiple spaces
    joined = re.sub(r' {2,}', ' ', joined).strip()
    return joined


# ── NER extraction ────────────────────────────────────────────────────────────

def _ner_skills(text: str, ner) -> list[str]:
    """
    Run NER and collect entity words. Filter out any WordPiece artefacts (##).
    """
    entities = ner(text)
    skills: list[str] = []
    for ent in entities:
        word = ent["word"].strip()
        # Skip subword artefacts and very short tokens
        if "#" in word or len(word) < 2:
            continue
        skills.append(word)
    return skills


# ── WordPiece fallback ────────────────────────────────────────────────────────

def _wordpiece_split(text: str) -> list[str]:
    """
    Use BERT tokenizer WordPiece boundaries to split the cleaned text into
    individual words, then group consecutive words that look like a skill phrase
    (up to 4 words, no common stop-words as the sole content).
    """
    from transformers import BertTokenizer
    tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")

    tokens = tokenizer.tokenize(text.lower())

    # Reconstruct surface words from subword tokens
    words: list[str] = []
    buf = ""
    for tok in tokens:
        if tok.startswith("##"):
            buf += tok[2:]
        else:
            if buf:
                words.append(buf)
            buf = tok
    if buf:
        words.append(buf)

    # Preserve capitalisation from original text for output
    original_words = text.split()
    if len(original_words) == len(words):
        words = original_words
    else:
        words = original_words  # prefer original if counts differ

    stop = {"or", "and", "the", "a", "an", "of", "in", "for"}

    merged: list[str] = []
    i = 0
    while i < len(words):
        # Try to build the longest phrase (up to 4 words) that makes sense
        best_phrase = words[i]
        best_end = i + 1
        for span in range(4, 1, -1):
            if i + span > len(words):
                continue
            candidate = " ".join(words[i:i + span])
            inner = set(candidate.lower().split()) - stop
            # Accept multi-word phrase if content words >= span - 1
            if len(inner) >= span - 1:
                best_phrase = candidate
                best_end = i + span
                break
        merged.append(best_phrase)
        i = best_end

    return [p for p in merged if p.strip()]


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_skills(fragments: list[str], ner) -> list[str]:
    cleaned = _clean_joined(fragments)
    print(f"  cleaned text : {cleaned!r}")

    # Try NER first
    skills = _ner_skills(cleaned, ner)
    if skills:
        print(f"  NER skills   : {skills}")
        return skills

    # Fallback: WordPiece word-boundary split
    skills = _wordpiece_split(cleaned)
    print(f"  WP  skills   : {skills}")
    return skills if skills else [cleaned]


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    from transformers import pipeline
    print("[fix_skills] Loading dslim/bert-base-NER …")
    ner = pipeline(
        "token-classification",
        model="dslim/bert-base-NER",
        aggregation_strategy="simple",
        device=-1,
    )

    db = SessionLocal()
    try:
        rows = db.query(RequisitionRequiredSkill).all()

        groups: dict[tuple[int, str], list[RequisitionRequiredSkill]] = defaultdict(list)
        for row in rows:
            groups[(row.requisition_id, row.skill_type or "required")].append(row)

        for (req_id, skill_type), group_rows in groups.items():
            fragments = [r.skill_name for r in group_rows]
            print(f"\nJR {req_id} [{skill_type}] — {len(fragments)} fragments: {fragments}")

            skills = extract_skills(fragments, ner)

            if dry_run:
                print(f"  → DRY RUN — would write: {skills}")
                continue

            for row in group_rows:
                db.delete(row)

            for skill_name in skills:
                db.add(RequisitionRequiredSkill(
                    requisition_id=req_id,
                    skill_name=skill_name,
                    skill_type=skill_type,
                ))

            db.commit()
            print(f"  → Written to DB: {skills}")

    finally:
        db.close()

    print("\n[fix_skills] Done.")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
