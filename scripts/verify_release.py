#!/usr/bin/env python3
"""Validate a VisDocBench lite release package.

Checks performed:
  * manifest structure and unique target paths
  * every annotation path starts with ``VisDocBench-parsed/`` and maps to a
    document in the manifest
  * SHA256SUMS matches every shipped file
  * no machine-specific absolute paths leak into text files
  * required files (annotations, README, tools) are present

Usage:
    python verify_release.py
    python verify_release.py --release release --strict
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

REQUIRED_DOC_KEYS = {"doc_id", "category", "doc_dir", "source", "source_id",
                     "target_path", "downloadable", "in_annotations", "in_mini_bench"}
ABS_PATH_PATTERNS = [
    re.compile("/" + r"(?:Users|home)/[^/\s\"']+"),
    re.compile("/" + "sharedata" + "/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
]
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".csv", ".yaml", ".yml", ".sha256"}


def normalize_to_origin(path: str) -> str:
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


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Report:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checks: List[Tuple[str, str]] = []

    def ok(self, message: str) -> None:
        self.checks.append(("PASS", message))

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.checks.append(("WARN", message))

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.checks.append(("FAIL", message))


def check_manifest(release: str, report: Report):
    path = os.path.join(release, "manifest.json")
    if not os.path.exists(path):
        report.error("manifest.json missing")
        return None, set()
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    docs = manifest.get("documents")
    if not isinstance(docs, list) or not docs:
        report.error("manifest.documents is empty or invalid")
        return manifest, set()

    targets: Set[str] = set()
    problems = 0
    for entry in docs:
        missing = REQUIRED_DOC_KEYS - set(entry)
        if missing:
            problems += 1
            continue
        target = entry["target_path"]
        if target in targets:
            report.error(f"duplicate target_path: {target}")
        targets.add(target)
        if not target.startswith("VisDocBench-parsed/") or not target.endswith("_origin.pdf"):
            problems += 1
        if target.split("/")[-1] != f"{entry['doc_id']}_origin.pdf":
            problems += 1
        for key in ("landing_url", "pdf_url"):
            url = entry.get(key) or ""
            if url and not url.startswith(("http://", "https://")):
                problems += 1
    if problems:
        report.error(f"manifest: {problems} malformed document entries")
    else:
        report.ok(f"manifest: {len(docs)} documents, {len(targets)} unique targets")

    summary = manifest.get("summary") or {}
    if summary.get("documents") != len(docs):
        report.warn("manifest summary document count does not match entries")
    return manifest, targets


def check_annotations(release: str, targets: Set[str], report: Report) -> None:
    files = sorted(glob.glob(os.path.join(release, "annotations", "*.json")))
    if not files:
        report.error("no annotation files found")
        return
    total = 0
    unresolved = set()
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            report.error(f"{name}: invalid JSON ({exc})")
            continue
        if not isinstance(data, list):
            report.error(f"{name}: top-level value is not a list")
            continue
        total += len(data)
        bad = 0
        for item in data:
            candidates = []
            if item.get("file_path"):
                candidates.append(item["file_path"])
            candidates.extend(item.get("source_files") or [])
            for candidate in candidates:
                if not candidate.startswith("VisDocBench-parsed/"):
                    bad += 1
                    continue
                origin = normalize_to_origin(candidate)
                if origin not in targets:
                    unresolved.add(origin)
        if bad:
            report.error(f"{name}: {bad} path fields are not dataset-relative")
    if unresolved:
        report.error(f"annotations reference {len(unresolved)} documents absent "
                     f"from manifest (e.g. {sorted(unresolved)[:3]})")
    else:
        report.ok(f"annotations: {len(files)} files, {total} records, paths resolve to manifest")


def check_checksums(release: str, report: Report) -> None:
    path = os.path.join(release, "SHA256SUMS")
    if not os.path.exists(path):
        report.error("SHA256SUMS missing")
        return
    listed = 0
    failures = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            digest, _, rel = line.partition("  ")
            full = os.path.join(release, rel)
            if not os.path.exists(full):
                failures += 1
                continue
            if sha256_file(full) != digest:
                failures += 1
            listed += 1
    if failures:
        report.error(f"SHA256SUMS: {failures} mismatched/missing files")
    else:
        report.ok(f"SHA256SUMS: {listed} files verified")

    shipped = set()
    for dirpath, _dirnames, filenames in os.walk(release):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), release).replace(os.sep, "/")
            if rel in ("SHA256SUMS",) or rel.endswith(".tar.gz") or rel.endswith(".tar.gz.sha256"):
                continue
            shipped.add(rel)
    with open(path, encoding="utf-8") as fh:
        covered = {line.rstrip("\n").partition("  ")[2] for line in fh if line.strip()}
    missing = shipped - covered
    if missing:
        report.warn(f"{len(missing)} shipped files not covered by SHA256SUMS")


def check_layout(release: str, report: Report) -> None:
    required = ["README.md", "manifest.json", "download_data.py", "verify_release.py"]
    missing = [name for name in required if not os.path.exists(os.path.join(release, name))]
    if missing:
        report.error(f"missing required files: {missing}")
    else:
        report.ok("layout: required files present")


def scan_leaks(release: str, report: Report) -> None:
    hits = []
    for dirpath, _dirnames, filenames in os.walk(release):
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in TEXT_SUFFIXES:
                continue
            if name == "verify_release.py":
                # this file contains the patterns themselves
                continue
            path = os.path.join(dirpath, name)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for pattern in ABS_PATH_PATTERNS:
                match = pattern.search(text)
                if match:
                    rel = os.path.relpath(path, release)
                    hits.append(f"{rel}: {match.group(0)[:60]}")
    if hits:
        report.error(f"absolute paths found in {len(hits)} place(s), e.g. {hits[:3]}")
    else:
        report.ok("path scan: no machine-specific absolute paths")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a VisDocBench lite release")
    parser.add_argument("--release", default=None)
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args()

    if args.release:
        release = os.path.abspath(args.release)
    elif os.path.exists(os.path.join(HERE, "manifest.json")):
        release = HERE
    else:
        release = os.path.join(REPO_ROOT, "release")

    if not os.path.isdir(release):
        raise SystemExit(f"release directory not found: {release}")

    print(f"verifying: {release}\n")
    report = Report()
    check_layout(release, report)
    _manifest, targets = check_manifest(release, report)
    if targets:
        check_annotations(release, targets, report)
    check_checksums(release, report)
    scan_leaks(release, report)

    for status, message in report.checks:
        print(f"  [{status}] {message}")
    print(f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    if report.errors or (args.strict and report.warnings):
        sys.exit(1)


if __name__ == "__main__":
    main()
