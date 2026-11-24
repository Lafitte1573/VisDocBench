#!/usr/bin/env python3
"""Build the VisDocBench *lite* release: annotations + provenance manifest + tools.

The lite release contains **no source documents**.  It ships:

    release/
    ├── annotations/            # 9 normalized annotation files (JSON)
    ├── manifest.json           # one record per document: source id / URL / target path
    ├── download_data.py        # fetch original PDFs from their public sources
    ├── source_catalog.py
    ├── verify_release.py       # validate the package
    ├── README.md               # dataset card
    ├── SHA256SUMS
    └── VisDocBench-lite-vX.Y.tar.gz (optional)

Usage:
    python scripts/build_release.py
    python scripts/build_release.py --version 1.0 --hash-pdfs --no-tar

Data layout expected on the maintainer machine:

    <repo>/VisDocBench-parsed/...
    <repo>/post-processing/normal_labeled_annotated/*.json
    <repo>/mini-bench/data/*.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import shutil
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from source_catalog import describe, target_path  # noqa: E402

SUBSETS = ["chart_analysis", "cross_page_blocks", "interline_equation",
           "mm_multi_hop_qa", "mm_single_hop_qa", "multi_hop_qa",
           "needle_in_a_haystack", "single_hop_qa", "single_hop_qa_mm"]
ANNOTATION_FILES = [s + ".json" for s in SUBSETS]


def normalize_to_origin(path: str) -> str:
    """Map any annotation path to its ``..._origin.pdf`` dataset-relative path."""
    marker = "VisDocBench-parsed/"
    rel = path.split(marker, 1)[1] if marker in path else path.lstrip("/")
    for suffix, replacement in (
        (".md", "_origin.pdf"),
        ("_middle.json", "_origin.pdf"),
        ("_content_bbox_list.json", "_origin.pdf"),
        ("_content_list.json", "_origin.pdf"),
        (".json", "_origin.pdf"),
    ):
        if rel.endswith(suffix):
            rel = rel[: -len(suffix)] + replacement
            break
    return "VisDocBench-parsed/" + rel


def doc_dir_of(origin_rel: str) -> str:
    """``VisDocBench-parsed/a/b/auto/x_origin.pdf`` -> ``a/b``."""
    rel = origin_rel.split("VisDocBench-parsed/", 1)[1]
    return rel.split("/auto/", 1)[0]


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_docs(dataset_root: str) -> Dict[str, str]:
    """doc_dir -> absolute origin PDF path."""
    pattern = os.path.join(dataset_root, "VisDocBench-parsed", "**", "*_origin.pdf")
    docs = {}
    for path in glob.glob(pattern, recursive=True):
        rel = os.path.relpath(path, os.path.join(dataset_root, "VisDocBench-parsed"))
        doc_dir = rel.replace(os.sep, "/").split("/auto/", 1)[0]
        docs[doc_dir] = path
    return docs


def collect_paths(json_dir: str) -> Set[str]:
    """All origin-PDF dataset paths referenced by a directory of annotation files."""
    paths: Set[str] = set()
    for path in glob.glob(os.path.join(json_dir, "*.json")):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            continue
        for item in data:
            candidates = []
            if item.get("file_path"):
                candidates.append(item["file_path"])
            candidates.extend(item.get("source_files") or [])
            for candidate in candidates:
                if "VisDocBench-parsed/" in candidate:
                    paths.add(normalize_to_origin(candidate))
    return paths


def build_manifest(dataset_root: str, docs: Dict[str, str], annotation_docs: Set[str],
                   mini_docs: Set[str], hash_pdfs: bool) -> Dict[str, object]:
    documents: List[Dict[str, object]] = []
    for index, (doc_dir, pdf_path) in enumerate(sorted(docs.items())):
        info = describe(doc_dir)
        entry = {
            "doc_id": doc_dir.split("/")[-1],
            "category": doc_dir.split("/")[0],
            "doc_dir": doc_dir,
            "source": info["source"],
            "source_id": info["source_id"],
            "landing_url": info["landing_url"],
            "pdf_url": info["pdf_url"],
            "downloadable": info["downloadable"],
            "target_path": target_path(doc_dir),
            "in_annotations": doc_dir in annotation_docs,
            "in_mini_bench": doc_dir in mini_docs,
            "note": info["note"],
        }
        if hash_pdfs:
            entry["sha256"] = sha256_file(pdf_path)
        documents.append(entry)

    categories = Counter(d["category"] for d in documents)
    sources = Counter(d["source"] for d in documents)
    return {
        "benchmark": "VisDocBench",
        "version": None,  # filled by caller
        "license_note": (
            "VisDocBench does not redistribute source documents. Each entry points to the "
            "public origin (source id / landing URL / direct PDF URL where available). "
            "Document copyrights remain with their respective owners; check each source's "
            "license before use or redistribution."
        ),
        "annotation_subsets": SUBSETS,
        "documents": documents,
        "summary": {
            "documents": len(documents),
            "in_annotations": sum(1 for d in documents if d["in_annotations"]),
            "in_mini_bench": sum(1 for d in documents if d["in_mini_bench"]),
            "by_category": dict(sorted(categories.items())),
            "by_source": dict(sorted(sources.items())),
        },
    }


def write_readme(path: str, manifest: Dict[str, object], version: str) -> None:
    summary = manifest["summary"]
    lines = [
        "# VisDocBench — lite release",
        "",
        "## 中文简介",
        "",
        "VisDocBench 是面向文档理解的多模态评测基准（31,028 条标注、947 个文档、9 类任务）。",
        "本仓库为 **lite 发布包**：包含标注、文档来源清单（source id / URL / 目标路径）与",
        "下载脚本，**不包含原始 PDF**。使用前请先运行 `download_data.py` 从公开来源获取",
        "文档，或按 `manual_sources.csv` 手动补齐；原始文档版权归各来源所有。",
        "",
        f"Version **{version}**. This package contains **annotations and document "
        "provenance only — no source PDFs**.",
        "",
        "## Contents",
        "",
        "```",
        "annotations/            9 normalized annotation files (JSON)",
        "manifest.json           one record per document (source id / URL / target path)",
        "manual_sources.csv      documents without a stable public PDF URL",
        "download_data.py        fetch original PDFs from their public sources",
        "source_catalog.py       provenance rules used by the downloader",
        "verify_release.py       validate this package",
        "README.md               this dataset card",
        "SHA256SUMS              checksums for every shipped file",
        "```",
        "",
        "## Numbers",
        "",
        f"- documents: **{summary['documents']}**",
        f"- documents referenced by annotations: **{summary['in_annotations']}**",
        f"- documents used by mini-bench: **{summary['in_mini_bench']}**",
        "",
        "| category | documents |",
        "|---|---|",
    ]
    for category, count in summary["by_category"].items():
        lines.append(f"| {category} | {count} |")
    lines += ["", "| source | documents |", "|---|---|"]
    for source, count in summary["by_source"].items():
        lines.append(f"| {source} | {count} |")

    lines += [
        "",
        "## Getting the source documents",
        "",
        "```bash",
        "# into the current directory (creates VisDocBench-parsed/...)",
        "python download_data.py --root .",
        "",
        "# only the documents used by mini-bench, into mini-bench/assets/",
        "python download_data.py --assets --mini",
        "",
        "# see what would be fetched without downloading",
        "python download_data.py --root . --dry-run",
        "```",
        "",
        "`--assets` assumes this release directory sits next to `mini-bench/` in the",
        "project tree; otherwise use `--root <project_root>`.",
        "",
        "This package ships **no PDFs**, so a download (or manual placement) is required",
        "before running the benchmark.",
        "",
        "Automatically supported sources: arXiv, Google Patents, Project Gutenberg, Zenodo.",
        "Exchange filings (`fin-report`), statistics releases (`stats_bullets`) and local",
        "slide decks (`PPT/Slide`) have no stable public PDF URL; they are listed in",
        "`manual_sources.csv` after a download attempt and must be supplied manually at",
        "the path given by `target_path`.",
        "",
        "The expected layout is exactly what the benchmark reads:",
        "",
        "```",
        "VisDocBench-parsed/<category>/<doc>/auto/<doc>_origin.pdf",
        "```",
        "",
        "## Validating",
        "",
        "```bash",
        "python verify_release.py",
        "```",
        "",
        "It checks manifest structure, annotation path prefixes, manifest coverage,",
        "SHA256SUMS, and scans for machine-specific absolute paths.",
        "",
        "## Mini-bench",
        "",
        "The full benchmark has 31,028 annotations. An optional 300-item mini-bench",
        "subset (9 subsets x 20 items, MC + OQ) also lives in `mini-bench/` in the code",
        "repository. Download only the documents it needs into the project:",
        "",
        "```bash",
        "python download_data.py --assets --mini",
        "```",
        "",
        "then run `mini-bench/run_eval.sh` with `LIMIT=1` for a smoke test.",
        "",
        "## Annotations",
        "",
        "Each file is a JSON array in the VisDocBench benchmark format. Path fields",
        "(`file_path`, `source_files`) always start with `VisDocBench-parsed/` and are",
        "resolved against the project root at evaluation time.",
        "",
        "## License",
        "",
        "Annotation metadata is released for research use. Source documents keep their",
        "original licenses; this package does not grant rights to redistribute them.",
        "See `manifest.json` for per-document provenance.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_checksums(out_dir: str, skip: Iterable[str] = ()) -> str:
    skip = set(skip)
    rows = []
    for dirpath, _dirnames, filenames in os.walk(out_dir):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, out_dir).replace(os.sep, "/")
            if rel in skip or rel == "SHA256SUMS":
                continue
            rows.append(f"{sha256_file(full)}  {rel}")
    content = "\n".join(rows) + "\n"
    with open(os.path.join(out_dir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        fh.write(content)
    return content


def make_tarball(out_dir: str, version: str) -> Tuple[str, str]:
    name = f"VisDocBench-lite-v{version}.tar.gz"
    tar_path = os.path.join(out_dir, name)
    with tarfile.open(tar_path, "w:gz") as tar:
        for dirpath, _dirnames, filenames in os.walk(out_dir):
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, out_dir)
                if rel == name or rel == name + ".sha256":
                    continue
                tar.add(full, arcname=os.path.join("VisDocBench-lite", rel))
    digest = sha256_file(tar_path)
    with open(tar_path + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {name}\n")
    return tar_path, digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the lite VisDocBench release")
    parser.add_argument("--dataset-root", default=REPO_ROOT,
                        help="project root containing VisDocBench-parsed/")
    parser.add_argument("--annotations", default=os.path.join(
        REPO_ROOT, "post-processing", "normal_labeled_annotated"))
    parser.add_argument("--mini-data", default=os.path.join(REPO_ROOT, "mini-bench", "data"))
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "release"))
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--hash-pdfs", action="store_true",
                        help="add local SHA256 for each PDF (slower, ~4 GB read)")
    parser.add_argument("--no-tar", action="store_true")
    args = parser.parse_args()

    dataset_root = os.path.abspath(args.dataset_root)
    if not os.path.isdir(os.path.join(dataset_root, "VisDocBench-parsed")):
        raise SystemExit(f"VisDocBench-parsed not found under {dataset_root}")

    print("collecting documents ...")
    docs = collect_docs(dataset_root)
    annotation_paths = collect_paths(args.annotations)
    mini_paths = collect_paths(args.mini_data)
    annotation_docs = {doc_dir_of(p) for p in annotation_paths}
    mini_docs = {doc_dir_of(p) for p in mini_paths}
    missing = annotation_docs - set(docs)
    if missing:
        print(f"[warn] {len(missing)} annotated documents have no _origin.pdf: "
              f"{sorted(missing)[:5]}")

    print(f"documents: {len(docs)}  annotations: {len(annotation_docs)}  mini: {len(mini_docs)}")
    manifest = build_manifest(dataset_root, docs, annotation_docs, mini_docs, args.hash_pdfs)
    manifest["version"] = args.version

    out_dir = os.path.abspath(args.out)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    os.makedirs(os.path.join(out_dir, "annotations"))

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    manual = [d for d in manifest["documents"] if not d["downloadable"]]
    with open(os.path.join(out_dir, "manual_sources.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["doc_id", "category", "source", "source_id", "target_path", "note"])
        for entry in manual:
            writer.writerow([entry["doc_id"], entry["category"], entry["source"],
                             entry["source_id"], entry["target_path"], entry["note"]])
    print(f"manual sources: {len(manual)}")

    copied = []
    for name in ANNOTATION_FILES:
        src = os.path.join(args.annotations, name)
        if not os.path.exists(src):
            print(f"[warn] missing annotation file: {name}")
            continue
        shutil.copy2(src, os.path.join(out_dir, "annotations", name))
        copied.append(name)
    print(f"annotations copied: {len(copied)}")

    for tool in ("download_data.py", "source_catalog.py", "verify_release.py"):
        src = os.path.join(HERE, tool)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out_dir, tool))
        else:
            print(f"[warn] missing tool: {tool}")

    write_readme(os.path.join(out_dir, "README.md"), manifest, args.version)
    write_checksums(out_dir)
    print(f"release written to {out_dir}")

    if not args.no_tar:
        tar_path, digest = make_tarball(out_dir, args.version)
        print(f"tarball: {tar_path}")
        print(f"sha256:  {digest}")


if __name__ == "__main__":
    main()
