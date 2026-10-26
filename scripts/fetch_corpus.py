#!/usr/bin/env python3
"""Download the public test corpus and record provenance for each file.

Usage:
    pip install pypdf          # optional, enables page count and text-layer check
    python scripts/fetch_corpus.py                 # core set: download anything missing
    python scripts/fetch_corpus.py --all           # also fetch on-hold documents (for reading only; not for ingestion)
    python scripts/fetch_corpus.py --force         # re-download everything
    python scripts/fetch_corpus.py --only ICH-Q3DR2 FDA-NITRO-2024

Documents with no pdf_url must be saved by hand as data/corpus/originals/<doc_id>.pdf
(from their landing_page); the script then fingerprints them like the rest.

Reads data/sources/corpus_manifest.csv and writes (all under data/corpus/, which Git ignores):
    data/corpus/originals/<doc_id>.pdf     versioned originals (never edited)
    data/corpus/corpus_register.csv        manifest + sha256, size, pages, text layer, retrieved_at
    data/corpus/fetch_log.json             per-document result, including failures
"""
import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = REPO / "data" / "sources" / "corpus_manifest.csv"
OUT = REPO / "data" / "corpus"
ORIGINALS = OUT / "originals"

# Some regulator sites reject requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/pdf,*/*;q=0.8",
}

# A page with fewer extracted characters than this probably needs OCR.
LOW_TEXT_CHARS = 50


def download(url: str, dest: Path, retries: int = 3) -> None:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if not data.startswith(b"%PDF"):
                raise ValueError("response is not a PDF (site may have returned an HTML page)")
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return
        except Exception as err:  # noqa: BLE001 - report any failure and retry
            last_err = err
            time.sleep(2 * attempt)
    raise RuntimeError(f"download failed after {retries} attempts: {last_err}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inspect_pdf(path: Path) -> dict:
    """Page count and a rough text-layer check. Needs pypdf; skipped if missing."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"pages": "", "low_text_pages": "", "text_layer": "unknown (pypdf not installed)"}
    reader = PdfReader(str(path))
    low = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a damaged page counts as needing OCR
            text = ""
        if len(text.strip()) < LOW_TEXT_CHARS:
            low.append(i)
    n = len(reader.pages)
    if not low:
        layer = "yes"
    elif len(low) == n:
        layer = "no (needs OCR)"
    else:
        layer = "partial"
    return {
        "pages": n,
        "low_text_pages": " ".join(map(str, low[:30])) + (" ..." if len(low) > 30 else ""),
        "text_layer": layer,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-download files that already exist")
    ap.add_argument("--only", nargs="*", help="doc_ids to process")
    ap.add_argument("--all", action="store_true", help="include reserve documents, not just the core set")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args()

    ORIGINALS.mkdir(parents=True, exist_ok=True)
    with args.manifest.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.only:
        rows = [r for r in rows if r["doc_id"] in set(args.only)]
    elif not args.all:
        rows = [r for r in rows if r.get("tier", "core") == "core"]
    held = [r["doc_id"] for r in rows if r.get("eligible", "yes") != "yes"]
    if held:
        print("WARNING: these documents are on hold (licence not cleared) and must NOT be ingested: "
              + ", ".join(held), file=sys.stderr)

    log, register = [], []
    for row in rows:
        doc_id = row["doc_id"]
        dest = ORIGINALS / f"{doc_id}.pdf"
        entry = {"doc_id": doc_id, "url": row["pdf_url"]}
        try:
            if not row["pdf_url"].strip():
                if not dest.exists():
                    entry["result"] = "MANUAL: save the PDF from landing_page as " + str(dest.relative_to(REPO))
                    print(f"[todo] {doc_id}: manual download needed ({row['landing_page']})")
                    log.append(entry)
                    register.append({**row, **{k: entry.get(k, "") for k in
                                     ("retrieved_at", "sha256", "bytes", "pages", "text_layer", "low_text_pages", "result")}})
                    continue
                print(f"[have] {doc_id} (manual)")
                entry["retrieved_at"] = datetime.fromtimestamp(
                    dest.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            elif args.force or not dest.exists():
                print(f"[get ] {doc_id}")
                download(row["pdf_url"], dest)
                entry["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            else:
                print(f"[have] {doc_id}")
                entry["retrieved_at"] = datetime.fromtimestamp(
                    dest.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            entry.update(sha256=sha256(dest), bytes=dest.stat().st_size, **inspect_pdf(dest))
            entry["result"] = "ok"
        except Exception as err:  # noqa: BLE001 - record and carry on with the rest
            entry["result"] = f"FAILED: {err}"
            print(f"[fail] {doc_id}: {err}", file=sys.stderr)
        log.append(entry)
        register.append({**row, **{k: entry.get(k, "") for k in
                         ("retrieved_at", "sha256", "bytes", "pages", "text_layer", "low_text_pages", "result")}})

    fields = list(register[0].keys()) if register else []
    with (OUT / "corpus_register.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(register)
    (OUT / "fetch_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")

    manual = [e["doc_id"] for e in log if e["result"].startswith("MANUAL")]
    failed = [e["doc_id"] for e in log if e["result"].startswith("FAILED")]
    ok = len(log) - len(manual) - len(failed)
    needs_ocr = [e["doc_id"] for e in log if str(e.get("text_layer", "")).startswith(("no", "partial"))]
    print(f"\n{ok}/{len(log)} in place. Register: {OUT / 'corpus_register.csv'}")
    if needs_ocr:
        print("Check text layer (OCR may be needed):", ", ".join(needs_ocr))
    if manual:
        print("Manual download needed (see landing_page and flags in the manifest):", ", ".join(manual))
    if failed:
        print("Failed (download manually from the landing_page and save as "
              "data/corpus/originals/<doc_id>.pdf):",
              ", ".join(failed))
    return 1 if (failed or manual) else 0


if __name__ == "__main__":
    sys.exit(main())
