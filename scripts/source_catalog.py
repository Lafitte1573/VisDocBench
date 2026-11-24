#!/usr/bin/env python3
"""Describe the provenance of a VisDocBench document directory.

The parsed dataset keeps one directory per source document:

    VisDocBench-parsed/<category>/[<subcategory>/]<doc_id>/auto/<doc_id>_origin.pdf

This module turns that directory into a manifest record (source type, source id,
landing URL, direct PDF URL where one exists, and whether the PDF is
downloadable automatically).  No PDF is redistributed by VisDocBench: the
manifest only records where the document came from.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

GUTENBERG_RE = re.compile(r"^pg(\d+)(-images)?$")
ZENODO_RE = re.compile(r"^[0-9a-f]{32}-(.+)$")


def describe(doc_dir: str) -> Dict[str, Any]:
    """Return provenance fields for a dataset-relative document directory."""
    doc_dir = doc_dir.replace(os.sep, "/").strip("/")
    parts = doc_dir.split("/")
    category = parts[0]
    doc_id = parts[-1]

    record: Dict[str, Any] = {
        "source": "unknown",
        "source_id": doc_id,
        "landing_url": "",
        "pdf_url": None,
        "downloadable": False,
        "note": "",
    }

    if category == "arxiv_pdfs" and len(parts) >= 3:
        arxiv_id = doc_id
        record.update(
            source="arxiv",
            source_id=arxiv_id,
            landing_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            downloadable=True,
        )
        return record

    if category == "patents_pdf":
        record.update(
            source="google_patents",
            source_id=doc_id,
            landing_url=f"https://patents.google.com/patent/{doc_id}/en",
            pdf_url=None,
            downloadable=True,
            note="PDF 链接需从 Google Patents 页面解析",
        )
        return record

    if category == "e-books-en":
        match = GUTENBERG_RE.match(doc_id)
        if match:
            number = match.group(1)
            record.update(
                source="project_gutenberg",
                source_id=number,
                landing_url=f"https://www.gutenberg.org/ebooks/{number}",
                pdf_url=None,
                downloadable=True,
                note="依次尝试 Gutenberg 的 PDF 路径；部分电子书无官方 PDF",
            )
        return record

    if category == "PPT" and len(parts) >= 3 and parts[1] == "Zenodo-tiny":
        match = ZENODO_RE.match(doc_id)
        title = match.group(1) if match else doc_id
        record.update(
            source="zenodo",
            source_id=doc_id,
            landing_url=f"https://zenodo.org/search?q={quote_plus(title)}",
            pdf_url=None,
            downloadable=True,
            note="通过 Zenodo API 按标题检索 PDF",
        )
        return record

    if category == "fin-report":
        record.update(
            source="cninfo",
            source_id=doc_id,
            landing_url="https://www.cninfo.com.cn/",
            downloadable=False,
            note="基金/上市公司公告，请按 source_id 在巨潮资讯网检索后手动放置",
        )
        return record

    if category == "stats_bullets":
        record.update(
            source="stats_gov_cn",
            source_id=doc_id,
            landing_url="https://www.stats.gov.cn/",
            downloadable=False,
            note="国家统计局新闻稿/公报，请按标题检索后手动放置",
        )
        return record

    if category == "PPT" and len(parts) >= 3 and parts[1] == "Slide":
        record.update(
            source="local_slides",
            source_id=doc_id,
            landing_url="",
            downloadable=False,
            note="教材/课程幻灯片，无公开下载地址，需手动提供",
        )
        return record

    record["note"] = "未知来源，需手动提供 PDF"
    return record


def target_path(doc_dir: str) -> str:
    """Dataset-relative target path of the original PDF."""
    doc_dir = doc_dir.replace(os.sep, "/").strip("/")
    doc_id = doc_dir.split("/")[-1]
    return f"VisDocBench-parsed/{doc_dir}/auto/{doc_id}_origin.pdf"


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["arxiv_pdfs/cs/2603.03591", "patents_pdf/US11562304B2"]:
        print(arg, "->", describe(arg))
