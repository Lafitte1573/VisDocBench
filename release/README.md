# VisDocBench — lite release

## 中文简介

VisDocBench 是面向文档理解的多模态评测基准（31,028 条标注、947 个文档、9 类任务）。
代码仓库：<https://github.com/Lafitte1573/VisDocBench>。

本仓库为 **lite 发布包**：包含标注、文档来源清单（source id / URL / 目标路径）与
下载脚本，**不包含原始 PDF**。使用前请先运行 `download_data.py` 从公开来源获取
文档，或按 `manual_sources.csv` 手动补齐；原始文档版权归各来源所有。

Version **1.0**. This package contains **annotations and document provenance only — no source PDFs**.

## Contents

```
annotations/            9 normalized annotation files (JSON)
manifest.json           one record per document (source id / URL / target path)
manual_sources.csv      documents without a stable public PDF URL
download_data.py        fetch original PDFs from their public sources
source_catalog.py       provenance rules used by the downloader
verify_release.py       validate this package
README.md               this dataset card
SHA256SUMS              checksums for every shipped file
```

## Numbers

- documents: **947**
- documents referenced by annotations: **896**
- documents used by mini-bench: **364**

| category | documents |
|---|---|
| PPT | 328 |
| arxiv_pdfs | 199 |
| e-books-en | 85 |
| fin-report | 67 |
| patents_pdf | 210 |
| stats_bullets | 58 |

| source | documents |
|---|---|
| arxiv | 199 |
| cninfo | 67 |
| google_patents | 210 |
| local_slides | 40 |
| project_gutenberg | 85 |
| stats_gov_cn | 58 |
| zenodo | 288 |

## Getting the source documents

```bash
# into the current directory (creates VisDocBench-parsed/...)
python download_data.py --root .

# only the documents used by mini-bench, into mini-bench/assets/
python download_data.py --assets --mini

# see what would be fetched without downloading
python download_data.py --root . --dry-run
```

`--assets` assumes this release directory sits next to `mini-bench/` in the
project tree; otherwise use `--root <project_root>`.

This package ships **no PDFs**, so a download (or manual placement) is required
before running the benchmark.

Automatically supported sources: arXiv, Google Patents, Project Gutenberg, Zenodo.
Exchange filings (`fin-report`), statistics releases (`stats_bullets`) and local
slide decks (`PPT/Slide`) have no stable public PDF URL; they are listed in
`manual_sources.csv` after a download attempt and must be supplied manually at
the path given by `target_path`.

The expected layout is exactly what the benchmark reads:

```
VisDocBench-parsed/<category>/<doc>/auto/<doc>_origin.pdf
```

## Validating

```bash
python verify_release.py
```

It checks manifest structure, annotation path prefixes, manifest coverage,
SHA256SUMS, and scans for machine-specific absolute paths.

## Mini-bench

The full benchmark has 31,028 annotations. An optional 300-item mini-bench
subset (9 subsets x 20 items, MC + OQ) also lives in `mini-bench/` in the code
repository. Download only the documents it needs into the project:

```bash
python download_data.py --assets --mini
```

then run `mini-bench/run_eval.sh` with `LIMIT=1` for a smoke test.

## Annotations

Each file is a JSON array in the VisDocBench benchmark format. Path fields
(`file_path`, `source_files`) always start with `VisDocBench-parsed/` and are
resolved against the project root at evaluation time.

## License

Annotation metadata is released for research use. Source documents keep their
original licenses; this package does not grant rights to redistribute them.
See `manifest.json` for per-document provenance.
