#!/usr/bin/env python3
"""
trial-eval: fetch_materials.py

Downloads each source listed in materials/manifest.json into materials/cache/
in two forms:
  - the raw bytes (PDF or HTML), preserved for provenance and SHA verification
  - a cleaned UTF-8 text version (<id>.txt) — this is what run_eval.py feeds
    to the models. PDFs are extracted with pypdf; HTML is stripped of nav,
    script, and style elements via BeautifulSoup.

Writes materials/cache/index.json mapping source id -> raw path, text path,
SHAs, byte counts, and retrieval timestamp. The whole cache directory is
git-ignored.

Dependencies: requests, pypdf, beautifulsoup4
    pip install requests pypdf beautifulsoup4

Usage:
    python fetch_materials.py                # fetch all sources
    python fetch_materials.py --phase 1      # only phase 1 sources
    python fetch_materials.py --force        # re-download even if cached
"""

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "materials" / "manifest.json"
CACHE = ROOT / "materials" / "cache"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; trial-eval research bot; contact via repo)"
}


def filename_for(source: dict) -> str:
    """Pick a sensible filename for the raw download. Defaults by content type
    when the URL path doesn't carry a clean .pdf/.html extension."""
    parsed = urlparse(source["url"])
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in (".pdf", ".html", ".htm", ".txt", ".json"):
        # Fallback: assume HTML for web pages, PDF for filings.
        suffix = ".pdf" if "pdf" in source["url"].lower() else ".html"
    return f"{source['id']}{suffix}"


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = f"[page {i + 1}: extraction failed: {e}]"
        pages.append(f"--- page {i + 1} ---\n{text.strip()}")
    return "\n\n".join(pages)


def extract_html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    # Strip noise. We're not trying to be fancy — just remove obvious chrome.
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]):
        tag.decompose()
    # Prefer <article> or <main> if present; else fall back to <body>.
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    lines = [line for line in (l.strip() for l in text.splitlines()) if line]
    return "\n".join(lines)


def extract_text(raw_path: Path, data: bytes) -> str:
    suffix = raw_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(data)
    if suffix in (".html", ".htm"):
        return extract_html_text(data)
    # plain text or unknown — best effort
    return data.decode("utf-8", errors="replace")


def fetch_one(source: dict, force: bool) -> dict:
    raw_path = CACHE / filename_for(source)
    text_path = CACHE / f"{source['id']}.txt"

    if raw_path.exists() and text_path.exists() and not force:
        raw = raw_path.read_bytes()
        return {
            "id": source["id"],
            "raw_path": str(raw_path.relative_to(ROOT)),
            "text_path": str(text_path.relative_to(ROOT)),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
            "text_chars": len(text_path.read_text(encoding="utf-8")),
            "retrieved_at": "cached",
            "url": source["url"],
        }

    print(f"  fetching {source['id']} <- {source['url']}")
    resp = requests.get(source["url"], headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.content
    raw_path.write_bytes(data)

    text = extract_text(raw_path, data)
    text_path.write_text(text, encoding="utf-8")

    return {
        "id": source["id"],
        "raw_path": str(raw_path.relative_to(ROOT)),
        "text_path": str(text_path.relative_to(ROOT)),
        "raw_sha256": hashlib.sha256(data).hexdigest(),
        "raw_bytes": len(data),
        "text_chars": len(text),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "url": source["url"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2, 3])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    sources = manifest["sources"]
    if args.phase:
        sources = [s for s in sources if args.phase in s.get("phases", [])]

    print(f"\nFetching {len(sources)} source(s) into {CACHE}\n")
    index = []
    failed = []
    for source in sources:
        try:
            entry = fetch_one(source, args.force)
            print(f"    -> {entry['raw_bytes']:,} raw bytes, {entry['text_chars']:,} text chars")
            index.append(entry)
        except Exception as e:
            print(f"  FAILED {source['id']}: {e}", file=sys.stderr)
            failed.append({"id": source["id"], "url": source["url"], "error": str(e)})

    (CACHE / "index.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fetched": index,
        "failed": failed,
    }, indent=2))
    print(f"\nDone. {len(index)} cached, {len(failed)} failed.")
    print(f"Index written to {CACHE / 'index.json'}")


if __name__ == "__main__":
    main()
