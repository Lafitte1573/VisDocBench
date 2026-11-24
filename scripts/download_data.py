#!/usr/bin/env python3
"""Download original source PDFs referenced by the VisDocBench manifest.

VisDocBench does **not** redistribute source documents.  This script reads the
release manifest (source type / source id / URL per document) and fetches each
PDF from its public origin, placing it at the path the benchmark expects:

    <root>/VisDocBench-parsed/<category>/<doc>/auto/<doc>_origin.pdf

Supported automatically
-----------------------
* arXiv papers          (``https://arxiv.org/pdf/<id>``)
* Google Patents        (PDF link parsed from the patent page)
* Project Gutenberg     (known PDF URL patterns)
* Zenodo                (search API by title/filename)

Other categories (exchange filings, statistics releases, local slide decks)
have no stable public PDF URL; the manifest marks them ``downloadable: false``
and they are written to ``manual_sources.csv`` for manual collection.

Examples
--------
    # fetch the 300-item mini-bench documents into mini-bench/assets/
    python download_data.py --assets --mini

    # fetch everything into the current project directory
    python download_data.py --root .

    # only arXiv + patents, 10 docs, show what would happen
    python download_data.py --only arxiv,google_patents --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from source_catalog import describe  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 VisDocBench/1.0")
PDF_MAGIC = b"%PDF"
PRINT_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": UA,
                                        "Accept": "application/pdf,*/*;q=0.8"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _looks_like_pdf(data: bytes) -> bool:
    return data[:4] == PDF_MAGIC


# --------------------------------------------------------------------------- #
# source adapters
# --------------------------------------------------------------------------- #
def fetch_arxiv(entry: Dict[str, Any], timeout: int) -> Tuple[Optional[bytes], str]:
    url = entry.get("pdf_url") or f"https://arxiv.org/pdf/{entry['source_id']}"
    data = http_get(url, timeout=timeout)
    return (data, url) if _looks_like_pdf(data) else (None, f"not a PDF: {url}")


def fetch_google_patents(entry: Dict[str, Any], timeout: int) -> Tuple[Optional[bytes], str]:
    html = http_get(entry["landing_url"], timeout=timeout).decode("utf-8", "ignore")
    match = re.search(r'citation_pdf_url"\s+content="([^"]+)"', html)
    if not match:
        match = re.search(r"(https://patentimages\.storage\.googleapis\.com/[^\"'\s]+?\.pdf)", html)
    if not match:
        return None, "PDF link not found on Google Patents page"
    pdf_url = match.group(1).replace("&amp;", "&")
    data = http_get(pdf_url, timeout=timeout)
    return (data, pdf_url) if _looks_like_pdf(data) else (None, f"not a PDF: {pdf_url}")


def fetch_gutenberg(entry: Dict[str, Any], timeout: int) -> Tuple[Optional[bytes], str]:
    n = entry["source_id"]
    candidates = [
        f"https://www.gutenberg.org/cache/epub/{n}/pg{n}.pdf",
        f"https://www.gutenberg.org/files/{n}/{n}-pdf.pdf",
        f"https://www.gutenberg.org/files/{n}/{n}-0.pdf",
    ]
    errors = []
    for url in candidates:
        try:
            data = http_get(url, timeout=timeout)
        except Exception as exc:  # 404 / 503 / timeout
            errors.append(f"{url}: {exc}")
            continue
        if _looks_like_pdf(data):
            return data, url
        errors.append(f"{url}: not a PDF")
    return None, "; ".join(errors[-2:])


def fetch_zenodo(entry: Dict[str, Any], timeout: int) -> Tuple[Optional[bytes], str]:
    title = entry["source_id"]
    match = re.match(r"^[0-9a-f]{32}-(.+)$", title)
    if match:
        title = match.group(1)
    api = f"https://zenodo.org/api/records?q={quote_plus(title)}&size=5"
    raw = http_get(api, timeout=timeout)
    payload = json.loads(raw.decode("utf-8", "ignore"))
    for hit in payload.get("hits", {}).get("hits", []):
        for file_entry in hit.get("files", []):
            key = file_entry.get("key", "")
            link = (file_entry.get("links") or {}).get("self", "")
            if key.lower().endswith(".pdf") and link:
                data = http_get(link, timeout=timeout)
                if _looks_like_pdf(data):
                    return data, link
    return None, "no PDF file found via Zenodo search"


ADAPTERS = {
    "arxiv": fetch_arxiv,
    "google_patents": fetch_google_patents,
    "project_gutenberg": fetch_gutenberg,
    "zenodo": fetch_zenodo,
}


# --------------------------------------------------------------------------- #
# job handling
# --------------------------------------------------------------------------- #
def load_manifest(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    docs = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(docs, list):
        raise ValueError("manifest must contain a 'documents' list")
    return docs


def select(docs: List[Dict[str, Any]], args) -> List[Dict[str, Any]]:
    selected = docs
    if args.mini:
        selected = [d for d in selected if d.get("in_mini_bench")]
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        selected = [d for d in selected if d.get("doc_id") in wanted]
    if args.only:
        wanted = {x.strip().lower() for x in args.only.split(",") if x.strip()}
        selected = [d for d in selected
                    if d.get("source", "").lower() in wanted
                    or d.get("category", "").lower() in wanted]
    if args.limit and args.limit > 0:
        selected = selected[: args.limit]
    return selected


def download_one(entry: Dict[str, Any], root: str, args) -> Dict[str, Any]:
    target = os.path.join(root, entry["target_path"])
    result = {
        "doc_id": entry.get("doc_id"),
        "source": entry.get("source"),
        "target": entry["target_path"],
        "status": "failed",
        "detail": "",
    }
    if os.path.exists(target) and not args.overwrite:
        result.update(status="skipped", detail="target exists")
        return result

    source = entry.get("source")
    if not entry.get("downloadable") or source not in ADAPTERS:
        result.update(status="manual", detail=entry.get("note")
                      or "no public PDF URL; download manually")
        return result
    if args.dry_run:
        url = entry.get("pdf_url") or entry.get("landing_url")
        result.update(status="planned", detail=url or "")
        return result

    try:
        data, detail = ADAPTERS[source](entry, args.timeout)
    except Exception as exc:
        result.update(status="failed", detail=f"{type(exc).__name__}: {exc}")
        return result
    if not data:
        result.update(status="failed", detail=detail)
        return result

    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + f".{os.getpid()}.part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, target)
    result.update(status="ok", detail=detail, bytes=len(data))
    return result


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def find_manifest(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    for candidate in (os.path.join(HERE, "manifest.json"),
                      os.path.join(REPO_ROOT, "release", "manifest.json")):
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("找不到 manifest.json；请用 --manifest 指定。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download original PDFs from source URLs")
    parser.add_argument("--manifest", default=None, help="release manifest.json")
    parser.add_argument("--root", default=None,
                        help="project root for VisDocBench-parsed/ (default: ./)")
    parser.add_argument("--assets", action="store_true",
                        help="download into mini-bench/assets/ (repo layout only)")
    parser.add_argument("--mini", action="store_true", help="only documents used by mini-bench")
    parser.add_argument("--ids", default="", help="comma separated doc ids")
    parser.add_argument("--only", default="", help="comma separated sources/categories")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = find_manifest(args.manifest)
    docs = select(load_manifest(manifest), args)

    if args.assets:
        root = os.path.join(REPO_ROOT, "mini-bench", "assets")
    else:
        root = os.path.abspath(args.root or os.getcwd())

    print(f"manifest: {os.path.relpath(manifest, os.getcwd())}")
    print(f"root:     {root}")
    print(f"documents:{len(docs)}  workers: {args.workers}  dry_run: {args.dry_run}\n")

    results: List[Dict[str, Any]] = []
    if args.workers > 1 and not args.dry_run:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_one, doc, root, args): doc for doc in docs}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                _log(result)
    else:
        for doc in docs:
            result = download_one(doc, root, args)
            results.append(result)
            _log(result)

    results.sort(key=lambda r: (r.get("status", ""), r.get("doc_id") or ""))
    summary: Dict[str, int] = {}
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1

    os.makedirs(root, exist_ok=True)
    report_path = os.path.join(root, "download_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump({"manifest": os.path.basename(manifest),
                   "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "root": ".",
                   "summary": summary,
                   "results": results}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    manual = [r for r in results if r["status"] == "manual"]
    if manual:
        manual_path = os.path.join(root, "manual_sources.csv")
        with open(manual_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["doc_id", "source", "target_path", "note"])
            by_id = {d["doc_id"]: d for d in docs}
            for item in manual:
                entry = by_id.get(item["doc_id"], {})
                writer.writerow([item["doc_id"], item["source"], item["target"],
                                 entry.get("note", "")])

    print("\n" + "=" * 60)
    print("download summary")
    print("=" * 60)
    for status in sorted(summary):
        print(f"  {status:10s} {summary[status]:5d}")
    print(f"\nreport: {report_path}")
    if manual:
        print(f"manual: {os.path.join(root, 'manual_sources.csv')}")
        print("未自动下载的文档请按 CSV 中的来源说明手动放置。")


def _log(result: Dict[str, Any]) -> None:
    with PRINT_LOCK:
        doc = result.get("doc_id") or "?"
        detail = (result.get("detail") or "").replace("\n", " ")[:100]
        print(f"  [{result['status']:8s}] {doc:36s} {detail}")


if __name__ == "__main__":
    main()
