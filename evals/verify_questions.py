#!/usr/bin/env python3
"""Check the golden questions against the downloaded PDFs. No domain expert needed.

For each question the script looks for the evidence in the PDF text itself:
  * answerable questions: finds the page(s) where all the anchor patterns appear
    in the expected document(s), and prints a text snippet so you can read the passage;
  * gap questions: confirms the terms do NOT appear anywhere in the corpus.

It proves the cited passage exists and where. It does NOT prove the answer is
scientifically right or that the question matters to a scientist. That is the job
of the developer read-through and the later scientist review (see data/sources/CORPUS.md).

Usage:
    pip install pypdf
    python evals/verify_questions.py
    python evals/verify_questions.py --questions evals/golden_questions.csv --originals data/corpus/originals

evidence_spec syntax (CSV column):
    DOC_ID::pattern1&&pattern2;;OTHER_DOC::pattern
Patterns are case-insensitive regular expressions matched against one page of text
with whitespace collapsed. All patterns of one DOC_ID must match on the same page.
For gap questions, the same syntax is used with DOC_ID = ANY (searches every PDF).
"""
import argparse
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def norm(text: str) -> str:
    text = text.replace(" ", " ").replace("‐", "-").replace("‑", "-")
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text)


_cache: dict[Path, list[str]] = {}


def pages_of(pdf: Path) -> list[str]:
    if pdf not in _cache:
        from pypdf import PdfReader
        out = []
        for page in PdfReader(str(pdf)).pages:
            try:
                out.append(norm(page.extract_text() or ""))
            except Exception:  # noqa: BLE001 - unreadable page counts as empty
                out.append("")
        _cache[pdf] = out
    return _cache[pdf]


def parse_spec(spec: str):
    for part in filter(None, (p.strip() for p in spec.split(";;"))):
        doc, _, pats = part.partition("::")
        yield doc.strip(), [p.strip() for p in pats.split("&&") if p.strip()]


def find_pages(pages: list[str], patterns: list[str]) -> list[int]:
    return [i + 1 for i, t in enumerate(pages) if all(re.search(p, t, re.I) for p in patterns)]


def snippet(pages: list[str], page_no: int, pattern: str, width: int = 220) -> str:
    text = pages[page_no - 1]
    m = re.search(pattern, text, re.I)
    if not m:
        return ""
    a = max(0, m.start() - width // 2)
    return ("..." + text[a:a + width] + "...").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=HERE / "golden_questions.csv")
    ap.add_argument("--originals", type=Path, default=REPO / "data" / "corpus" / "originals")
    ap.add_argument("--out", type=Path, default=HERE / "golden_questions_checked.csv")
    args = ap.parse_args()

    with args.questions.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    all_pdfs = sorted(args.originals.glob("*.pdf"))
    results, problems = [], 0

    for r in rows:
        behaviour = r["expected_behaviour"]
        found_bits, snippets, status = [], [], "OK"
        if behaviour == "gap":
            hits = []
            for _, pats in parse_spec(r["evidence_spec"]):
                for pdf in all_pdfs:
                    pg = find_pages(pages_of(pdf), pats)
                    if pg:
                        hits.append(f"{pdf.stem}:p{','.join(map(str, pg[:5]))}")
            if hits:
                status = "CHECK: terms found in corpus, so this may not be a true gap"
                found_bits = hits
            else:
                status = "OK: not in corpus"
        else:
            for doc, pats in parse_spec(r["evidence_spec"]):
                pdf = args.originals / f"{doc}.pdf"
                if not pdf.exists():
                    status = f"DOC_MISSING: {doc}"
                    found_bits.append(f"{doc}: not downloaded")
                    continue
                pages = pages_of(pdf)
                pg = find_pages(pages, pats)
                if pg:
                    found_bits.append(f"{doc}: p{','.join(map(str, pg[:6]))}")
                    snippets.append(f"[{doc} p{pg[0]}] " + snippet(pages, pg[0], pats[0]))
                else:
                    if not status.startswith("DOC_MISSING"):
                        status = "NOT_FOUND: no page matches all patterns (fix the question or the pattern)"
                    found_bits.append(f"{doc}: no match")
        if not status.startswith("OK"):
            problems += 1
        results.append({**r, "check_status": status, "evidence_pages": " | ".join(found_bits),
                        "evidence_snippet": " || ".join(snippets)})

    fields = list(results[0].keys()) if results else []
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    for r in results:
        print(f"{r['q_id']:7} {r['check_status'][:60]:60} {r['evidence_pages'][:60]}")
    print(f"\n{len(results) - problems}/{len(results)} questions OK. Details: {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
