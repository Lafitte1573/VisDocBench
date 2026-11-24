#!/usr/bin/env python3
"""VisDocBench evaluation (multiple-choice + open-question).

By default this runs the optional 300-item mini-bench subset (``data/``).
Pass ``--data-dir ../release/annotations`` to evaluate the full 31,028-item
VisDocBench benchmark with the same script.

One entry point for both task families:

  * MC  — 9 subsets, 4-option questions (chart / cross-page / equation / QA).
  * OQ  — 6 QA subsets that carry a reference answer, scored by an LLM judge.

Every sample is rendered to page images and sent to a multimodal
OpenAI-compatible chat endpoint.

Examples
--------
    LIMIT=1 python evaluate.py                 # smoke test, 1 item per subset
    python evaluate.py --limit 5 --workers 4   # 5 items per subset
    python evaluate.py                         # all items in data/

Environment
-----------
    OPENAI_BASE_URL   chat-completions endpoint (default: config.yaml)
    OPENAI_API_KEY    API key (default: config.yaml)
    OPENAI_MODEL      model id (default: config.yaml)
    VISDOCBENCH_ROOT  project root containing VisDocBench-parsed/
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF
import yaml
from openai import OpenAI

HERE = os.path.dirname(os.path.abspath(__file__))

SUBSETS = [
    "chart_analysis",
    "cross_page_blocks",
    "interline_equation",
    "mm_multi_hop_qa",
    "mm_single_hop_qa",
    "multi_hop_qa",
    "needle_in_a_haystack",
    "single_hop_qa",
    "single_hop_qa_mm",
]
QA_SUBSETS = [
    "mm_multi_hop_qa",
    "mm_single_hop_qa",
    "multi_hop_qa",
    "needle_in_a_haystack",
    "single_hop_qa",
    "single_hop_qa_mm",
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "dataset_root": "..",
    "assets_dir": "assets",
    "api": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 2048,
        "enable_thinking": False,
        "timeout": 180,
        "retries": 3,
    },
    "render": {
        "dpi": 120,
        "max_pages_total": 6,
        "max_pages_per_doc": 2,
        "max_doc_pages": 12,
    },
}

_FITZ_LOCK = threading.RLock()
_TEXT_CACHE: Dict[str, List[str]] = {}
_TOKEN_CACHE: Dict[str, List[set]] = {}

SYSTEM_MC = (
    "你是一个专业的文档分析助手。你将看到与题目相关的文档页面图片。\n"
    "请仔细阅读图片内容，结合题目与选项作答。\n"
    "输出格式要求：只输出一行 **答案：X**（X 为选项字母），不要输出任何解释。"
)
SYSTEM_OQ = (
    "你是一个专业的文档分析助手。你将看到与问题相关的文档页面图片。\n"
    "请根据文档内容回答问题，答案要简洁、准确。\n"
    "只输出答案本身，不要输出解释或推理过程。"
)
SYSTEM_JUDGE = (
    "你是严格的答案评分员。请判断【模型回答】与【标准答案】是否语义等价。\n"
    "数值、实体、结论一致即视为正确；表述方式不同、语言不同不算错；"
    "部分正确但遗漏关键信息视为错误。\n只输出 1（正确）或 0（错误），不要输出其他内容。"
)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    path = path or os.path.join(HERE, "config.yaml")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value

    root = os.getenv("VISDOCBENCH_ROOT") or cfg.get("dataset_root") or ".."
    if not os.path.isabs(root):
        root = os.path.abspath(os.path.join(HERE, root))
    cfg["dataset_root"] = root

    api = cfg["api"]
    for env_name, key in (("OPENAI_BASE_URL", "base_url"),
                          ("OPENAI_API_KEY", "api_key"),
                          ("OPENAI_MODEL", "model")):
        if os.getenv(env_name):
            api[key] = os.getenv(env_name)
    return cfg


def check_config(cfg: Dict[str, Any], require_api: bool = True) -> None:
    if not data_roots(cfg):
        sys.exit(
            "找不到 VisDocBench-parsed 数据。\n"
            f"已查找: {os.path.join(cfg['dataset_root'], 'VisDocBench-parsed')} "
            f"和 {os.path.join(HERE, cfg.get('assets_dir') or 'assets', 'VisDocBench-parsed')}\n"
            "请把数据放到项目根目录、下载到 mini-bench/assets/，"
            "或设置 VISDOCBENCH_ROOT / VISDOCBENCH_ASSETS。"
        )
    if not require_api:
        return
    if not cfg["api"]["model"]:
        sys.exit("请通过 OPENAI_MODEL 或 config.yaml 指定模型名称。")
    if not cfg["api"]["base_url"]:
        sys.exit("请通过 OPENAI_BASE_URL 或 config.yaml 指定 API 地址。")


# --------------------------------------------------------------------------- #
# dataset paths
# --------------------------------------------------------------------------- #
def data_roots(cfg: Dict[str, Any]) -> List[str]:
    """Dataset roots to search, most specific first.

    ``mini-bench/assets/`` (generated by ``download_data.py --assets``) is tried
    before the full ``<dataset_root>/VisDocBench-parsed`` tree.
    """
    roots: List[str] = []
    assets = os.getenv("VISDOCBENCH_ASSETS") or cfg.get("assets_dir")
    if assets:
        base = assets if os.path.isabs(assets) else os.path.join(HERE, assets)
        roots.append(os.path.join(base, "VisDocBench-parsed"))
    roots.append(os.path.join(cfg["dataset_root"], "VisDocBench-parsed"))
    unique: List[str] = []
    for root in roots:
        if os.path.isdir(root) and root not in unique:
            unique.append(root)
    return unique


def pdf_for_path(normalized: str, cfg: Dict[str, Any]) -> Optional[str]:
    """Map ``VisDocBench-parsed/**.md|*.json`` to its ``_origin.pdf`` sibling."""
    marker = "VisDocBench-parsed/"
    if marker not in normalized:
        return None
    rel = normalized.split(marker, 1)[1]
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
    for root in data_roots(cfg):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return path
    return None


# --------------------------------------------------------------------------- #
# PDF text, tokens, BM25 page retrieval
# --------------------------------------------------------------------------- #
def _doc_key(pdf_path: str) -> str:
    stat = os.stat(pdf_path)
    return f"{pdf_path}:{stat.st_mtime_ns}:{stat.st_size}"


def page_texts(pdf_path: str) -> List[str]:
    key = _doc_key(pdf_path)
    with _FITZ_LOCK:
        if key not in _TEXT_CACHE:
            with fitz.open(pdf_path) as doc:
                _TEXT_CACHE[key] = [page.get_text("text") or "" for page in doc]
        return _TEXT_CACHE[key]


def page_tokens(pdf_path: str) -> List[set]:
    key = _doc_key(pdf_path)
    with _FITZ_LOCK:
        if key not in _TOKEN_CACHE:
            _TOKEN_CACHE[key] = [_tokenize(t) for t in page_texts(pdf_path)]
        return _TOKEN_CACHE[key]


def doc_page_count(pdf_path: str) -> int:
    with _FITZ_LOCK:
        with fitz.open(pdf_path) as doc:
            return len(doc)


def _tokenize(text: str) -> set:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return set(words) | set(bigrams)


def _bm25(texts: List[str], query_tokens: set, k1: float = 1.2,
          b: float = 0.75) -> List[float]:
    if not texts or not query_tokens:
        return [0.0] * len(texts)
    docs = [_tokenize(t) for t in texts]
    n = len(docs)
    avgdl = (sum(len(d) for d in docs) / n) or 1.0
    df = Counter()
    for doc in docs:
        for token in query_tokens & doc:
            df[token] += 1
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in query_tokens if df[t]}
    scores = []
    for doc in docs:
        raw = sum(idf[t] for t in (query_tokens & doc) if t in idf)
        scores.append(raw / (1 + k1 * (1 - b + b * len(doc) / avgdl)))
    return scores


def retrieve_pages(pdf_path: str, query: str, k: int = 1) -> List[int]:
    """Top-k pages by BM25; fall back to the first pages when nothing matches."""
    texts = page_texts(pdf_path)
    if not texts:
        return []
    scores = _bm25(texts, _tokenize(query))
    ranked = sorted(range(len(texts)), key=lambda i: -scores[i])
    picked = [i for i in ranked[:k] if scores[i] > 0]
    return picked or list(range(min(k, len(texts))))


def find_snippet_page(pdf_path: str, snippet: str) -> Optional[int]:
    """Page containing an exact (whitespace-insensitive) snippet."""
    texts = page_texts(pdf_path)
    needle = re.sub(r"\s+", "", snippet).lower()
    if len(needle) < 12:
        return None
    for i, text in enumerate(texts):
        if needle[:120] in re.sub(r"\s+", "", text).lower():
            return i
    return None


def _evenly_sample(n: int, k: int) -> List[int]:
    if k >= n:
        return list(range(n))
    if k <= 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


# --------------------------------------------------------------------------- #
# evidence page selection
# --------------------------------------------------------------------------- #
class PageRef:
    __slots__ = ("pdf", "page", "mask_bbox", "mask_label")

    def __init__(self, pdf: str, page: int, mask_bbox=None, mask_label=None):
        self.pdf = pdf
        self.page = page
        self.mask_bbox = mask_bbox
        self.mask_label = mask_label


def _quote_query(item: Dict[str, Any]) -> str:
    parts = [str(item.get("question", ""))]
    if item.get("answer"):
        parts.append(str(item["answer"]))
    return "\n".join(parts)


def select_pages(subset: str, item: Dict[str, Any], cfg: Dict[str, Any]) -> List[PageRef]:
    render_cfg = cfg["render"]

    # cloze tasks: one annotated page with the bbox masked
    if subset in ("chart_analysis", "interline_equation"):
        pdf = pdf_for_path(item.get("file_path", ""), cfg)
        if not pdf:
            return []
        bbox = item.get("bbox")
        page = int(item.get("page", item.get("page_idx", 0)) or 0)
        label = "Paragraph 1" if subset == "chart_analysis" else "Equation 1"
        mask = tuple(bbox) if bbox and len(bbox) == 4 else None
        return [PageRef(pdf, page, mask, label)]

    # cross-page paragraph: shuffled subset of pages
    if subset == "cross_page_blocks":
        pdf = pdf_for_path(item.get("file_path", ""), cfg)
        if not pdf:
            return []
        n = doc_page_count(pdf)
        cap = int(render_cfg.get("max_doc_pages", 12))
        pre = item.get("pre_text", "") or ""
        focus = find_snippet_page(pdf, pre[:120]) if pre else None
        if focus is None:
            focus = retrieve_pages(pdf, pre, k=1)[0] if pre else 0
        idxs = _evenly_sample(n, min(cap, n))
        for must in (focus, min(focus + 1, n - 1)):
            if must not in idxs:
                idxs[-1] = must
                idxs = sorted(set(idxs))
        seed = hashlib.sha1(str(item.get("file_key", pdf)).encode()).hexdigest()
        random.Random(seed).shuffle(idxs)
        return [PageRef(pdf, i) for i in idxs]

    # QA / needle: BM25 evidence pages from each source document
    sources = item.get("source_files") or ([item["file_path"]] if item.get("file_path") else [])
    sources = list(dict.fromkeys(sources))
    per_doc = int(render_cfg.get("max_pages_per_doc", 2))
    total_cap = int(render_cfg.get("max_pages_total", 6))
    query = _quote_query(item)
    query_tokens = _tokenize(query)

    per_source: List[Tuple[str, List[int], float]] = []
    for src in sources:
        pdf = pdf_for_path(src, cfg)
        if not pdf:
            continue
        if subset == "needle_in_a_haystack" and item.get("source_quote"):
            idx = find_snippet_page(pdf, item["source_quote"])
            if idx is None:
                idxs = retrieve_pages(pdf, item["source_quote"] + "\n" + query, k=per_doc)
            else:
                idxs = [idx] + ([idx + 1] if idx + 1 < doc_page_count(pdf) else [])
        else:
            idxs = retrieve_pages(pdf, query, k=per_doc)
        texts = page_texts(pdf)
        scores = _bm25(texts, query_tokens)
        score = max((scores[i] for i in idxs if i < len(scores)), default=0.0)
        per_source.append((pdf, idxs, score))

    per_source.sort(key=lambda entry: -entry[2])
    pages: List[PageRef] = []
    for rank in range(per_doc):
        for pdf, idxs, _score in per_source:
            if rank < len(idxs) and len(pages) < total_cap:
                pages.append(PageRef(pdf, idxs[rank]))
    return pages


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_page(ref: PageRef, cfg: Dict[str, Any], cache_dir: str) -> Optional[str]:
    dpi = int(cfg["render"].get("dpi", 120))
    key = hashlib.sha1(
        f"{ref.pdf}|{ref.page}|{dpi}|{ref.mask_bbox}:{ref.mask_label}".encode()
    ).hexdigest()
    out_dir = os.path.join(cache_dir, "pages")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, key + ".png")
    if os.path.exists(out):
        return out

    with _FITZ_LOCK:
        try:
            with fitz.open(ref.pdf) as doc:
                if ref.page < 0 or ref.page >= len(doc):
                    return None
                page = doc[ref.page]
                if ref.mask_bbox:
                    rect = fitz.Rect(ref.mask_bbox)
                    page.draw_rect(rect, color=(1, 0, 0), fill=(1, 1, 1), width=1)
                    if ref.mask_label:
                        size = max(6, min(14, rect.width / 15, rect.height / 2))
                        page.insert_text(
                            fitz.Point(rect.x0 + 5, rect.y0 + rect.height / 2),
                            ref.mask_label, fontsize=size, color=(1, 0, 0),
                        )
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
                tmp = out[:-4] + f".{os.getpid()}.tmp.png"
                pix.save(tmp)
                os.replace(tmp, out)
        except Exception as exc:
            print(f"[render-error] {os.path.basename(ref.pdf)} p{ref.page}: {exc}")
            return None
    return out


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
def make_client(cfg: Dict[str, Any]) -> OpenAI:
    api = cfg["api"]
    return OpenAI(api_key=api.get("api_key") or "EMPTY",
                  base_url=api["base_url"],
                  timeout=api.get("timeout", 180))


def _image_part(path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def chat(client: OpenAI, cfg: Dict[str, Any], messages: List[Dict[str, Any]],
         max_tokens: Optional[int] = None) -> Dict[str, Any]:
    api = cfg["api"]
    attempts = int(api.get("retries", 3))
    last_err: Optional[str] = None
    started = time.time()
    for attempt in range(attempts):
        try:
            kwargs: Dict[str, Any] = dict(
                model=api["model"],
                messages=messages,
                temperature=float(api.get("temperature", 0.0)),
                top_p=float(api.get("top_p", 1.0)),
                max_tokens=int(max_tokens or api.get("max_tokens", 2048)),
            )
            if api.get("enable_thinking") is not None:
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": bool(api["enable_thinking"])}}
            resp = client.chat.completions.create(**kwargs)
            message = resp.choices[0].message
            content = (message.content or "").strip()
            reasoning = getattr(message, "reasoning", None) or ""
            if not content and reasoning:
                if attempt + 1 < attempts:
                    last_err = "empty content (truncated reasoning)"
                    max_tokens = int(max_tokens or api.get("max_tokens", 2048)) * 2
                    continue
                content = reasoning
            return {
                "content": content,
                "reasoning": reasoning,
                "finish_reason": resp.choices[0].finish_reason,
                "latency": round(time.time() - started, 2),
            }
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                time.sleep(min(8, 2 ** attempt))
    return {"content": "", "error": last_err, "latency": round(time.time() - started, 2)}


def chat_with_images(client: OpenAI, cfg: Dict[str, Any], image_paths: Sequence[str],
                     system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [_image_part(p) for p in image_paths]
    content.append({"type": "text", "text": user_prompt})
    return chat(client, cfg, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ])


# --------------------------------------------------------------------------- #
# prompts / parsing
# --------------------------------------------------------------------------- #
def choice_text(choice: Any) -> str:
    if isinstance(choice, (list, tuple)):
        return " ".join(str(x) for x in choice)
    return str(choice)


def options_block(choices: Sequence[Any]) -> str:
    letters = "ABCDEFG"
    return "\n".join(f"{letters[i]}. {choice_text(c)}" for i, c in enumerate(choices))


def build_mc_prompt(subset: str, item: Dict[str, Any]) -> str:
    opts = options_block(item.get("choices") or [])
    if subset == "chart_analysis":
        return (
            "请仔细阅读文档图片。以下文段中有部分关键信息被 [MASK] 遮盖，"
            "请从四个选项中选择最可能是被遮盖内容（按顺序替换文段中的所有 [MASK]）的答案。\n\n"
            f"### 文段内容：\n{item.get('masked_text', '')}\n\n{opts}\n\n"
            "请直接输出选项字母，例如：**答案：A**"
        )
    if subset == "interline_equation":
        return (
            "请仔细阅读文档图片。图中区域 `Equation 1` 的公式被遮盖，"
            "请从四个选项中选择最可能是该公式的答案。\n\n"
            f"{opts}\n\n请直接输出选项字母，例如：**答案：A**"
        )
    if subset == "cross_page_blocks":
        return (
            "这是一篇页码被打乱的文档，请仔细阅读文档图片并理解文档内容，"
            "然后从四个选项中选择最可能是以下文段后续内容的答案。\n\n"
            f"### 文段内容：\n{item.get('pre_text', '')}\n\n{opts}\n\n"
            "请直接输出选项字母，例如：**答案：A**"
        )
    return (
        "请根据文档图片回答下面的问题。\n\n"
        f"### 问题：\n{item.get('question', '')}\n\n{opts}\n\n"
        "请直接输出选项字母，例如：**答案：A**"
    )


def build_oq_prompt(item: Dict[str, Any]) -> str:
    return f"请根据文档内容回答问题，只输出答案。\n\n### 问题：\n{item.get('question', '')}"


def gold_letter(item: Dict[str, Any]) -> str:
    index = item.get("correct_index")
    letters = "ABCDEFG"
    if isinstance(index, int) and 0 <= index < len(letters):
        return letters[index]
    answer = item.get("answer")
    for i, choice in enumerate(item.get("choices") or []):
        if answer is not None and str(answer) == choice_text(choice):
            return letters[i]
    return "?"


_ANSWER_PAT = re.compile(r"答案\s*[:：]\s*\**\s*([A-Ga-g])")
_LETTER_PAT = re.compile(r"(?<![A-Za-z])([A-G])(?![A-Za-z])")


def parse_choice(text: str) -> str:
    if not text:
        return ""
    norm = text.translate(str.maketrans("ＡＢＣＤＥＦＧ", "ABCDEFG"))
    match = _ANSWER_PAT.search(norm)
    if match:
        return match.group(1).upper()
    letters = _LETTER_PAT.findall(norm)
    return letters[-1].upper() if letters else ""


def parse_judge(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"[01]", text)
    if match:
        return int(match.group(0))
    if any(w in text for w in ("正确", "等价", "一致")):
        return 1
    if any(w in text for w in ("错误", "不等价", "不一致")):
        return 0
    return None


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def token_f1(gold: str, pred: str) -> float:
    gold_tokens = _tokenize(str(gold))
    pred_tokens = _tokenize(str(pred))
    if not gold_tokens or not pred_tokens:
        return 0.0
    overlap = len(gold_tokens & pred_tokens)
    if not overlap:
        return 0.0
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return round(2 * precision * recall / (precision + recall), 4)


def subset_metrics(records: Iterable[Dict[str, Any]], key: str = "correct") -> Dict[str, Any]:
    records = list(records)
    total = len(records)
    correct = sum(1 for r in records if r.get(key))
    errors = sum(1 for r in records if r.get("error"))
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "errors": errors,
    }


def summarize(records: List[Dict[str, Any]], subsets: Sequence[str]) -> Dict[str, Any]:
    return {
        "overall": subset_metrics(records),
        "by_subset": {
            s: subset_metrics([r for r in records if r["subset"] == s]) for s in subsets
        },
    }


# --------------------------------------------------------------------------- #
# task evaluation
# --------------------------------------------------------------------------- #
def _prepare(subset: str, item: Dict[str, Any], cfg: Dict[str, Any]):
    pages = select_pages(subset, item, cfg)
    paths = [p for p in (render_page(ref, cfg, os.path.join(HERE, "cache")) for ref in pages) if p]
    return pages, paths


def evaluate_mc_item(subset: str, index: int, item: Dict[str, Any], cfg: Dict[str, Any],
                     client: Optional[OpenAI], dry_run: bool) -> Dict[str, Any]:
    started = time.time()
    record = {
        "subset": subset,
        "index": index,
        "file_key": item.get("file_key", ""),
        "question": (item.get("question") or item.get("masked_text")
                     or item.get("pre_text") or "")[:600],
        "gold": gold_letter(item),
        "predicted": "",
        "correct": False,
    }
    try:
        _pages, images = _prepare(subset, item, cfg)
        if not images:
            record.update(error="no page images", latency=round(time.time() - started, 2))
            return record
        record["num_images"] = len(images)
        prompt = build_mc_prompt(subset, item)
        if dry_run:
            record.update(dry_run=True, prompt=prompt)
            return record
        resp = chat_with_images(client, cfg, images, SYSTEM_MC, prompt)
        if resp.get("error"):
            record.update(error=resp["error"], latency=resp.get("latency"))
            return record
        predicted = parse_choice(resp["content"])
        record.update(
            predicted=predicted,
            correct=predicted == record["gold"],
            raw_output=resp["content"][:2000],
            finish_reason=resp.get("finish_reason"),
            latency=resp.get("latency"),
        )
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    return record


def _judge(client: OpenAI, cfg: Dict[str, Any], question: str, gold: str,
           predicted: str) -> Tuple[Optional[int], str]:
    user = (
        f"【问题】\n{question}\n\n"
        f"【标准答案】\n{gold}\n\n"
        f"【模型回答】\n{predicted}\n\n"
        "请判断模型回答是否与标准答案语义等价，只输出 1 或 0。"
    )
    resp = chat(client, cfg, [
        {"role": "system", "content": SYSTEM_JUDGE},
        {"role": "user", "content": user},
    ], max_tokens=64)
    if resp.get("error"):
        return None, resp["error"]
    return parse_judge(resp["content"]), resp["content"][:200]


def evaluate_oq_item(subset: str, index: int, item: Dict[str, Any], cfg: Dict[str, Any],
                     client: Optional[OpenAI], dry_run: bool) -> Dict[str, Any]:
    started = time.time()
    gold = str(item.get("answer", "")).strip()
    record = {
        "subset": subset,
        "index": index,
        "file_key": item.get("file_key", ""),
        "question": str(item.get("question", ""))[:600],
        "gold": gold,
        "predicted": "",
        "judge": None,
        "correct": False,
    }
    try:
        _pages, images = _prepare(subset, item, cfg)
        if not images:
            record.update(error="no page images", latency=round(time.time() - started, 2))
            return record
        record["num_images"] = len(images)
        prompt = build_oq_prompt(item)
        if dry_run:
            record.update(dry_run=True, prompt=prompt)
            return record
        resp = chat_with_images(client, cfg, images, SYSTEM_OQ, prompt)
        if resp.get("error"):
            record.update(error=resp["error"], latency=resp.get("latency"))
            return record
        predicted = resp["content"].strip()
        record.update(predicted=predicted, raw_output=predicted[:2000],
                      latency=resp.get("latency"), finish_reason=resp.get("finish_reason"))

        score, judge_raw = _judge(client, cfg, record["question"], gold, predicted)
        if score is None:
            f1 = token_f1(gold, predicted)
            score = 1 if (f1 >= 0.6 or _norm_text(gold) in _norm_text(predicted)) else 0
            record["judge_fallback"] = True
        else:
            record["judge_raw"] = judge_raw
        record.update(judge=score, correct=bool(score), f1=token_f1(gold, predicted))
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    return record


def run_task(task: str, cfg: Dict[str, Any], args) -> Tuple[List[Dict[str, Any]], List[str]]:
    subsets = [s.strip() for s in args.subsets.split(",") if s.strip()] if args.subsets else \
        (SUBSETS if task == "mc" else QA_SUBSETS)
    if task == "oq":
        subsets = [s for s in subsets if s in QA_SUBSETS]

    items_by_subset: List[Tuple[str, List[Dict[str, Any]]]] = []
    tasks: List[Tuple[str, int, Dict[str, Any]]] = []
    for subset in subsets:
        path = os.path.join(args.data_dir, subset + ".json")
        if not os.path.exists(path):
            print(f"[skip] {subset}: missing data file")
            continue
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
        if task == "oq":
            items = [it for it in items if "answer" in it]
        if args.limit > 0:
            items = items[: args.limit]
        items_by_subset.append((subset, items))
        tasks.extend((subset, i, it) for i, it in enumerate(items))

    print(f"\n=== {task.upper()} ===")
    print(f"items: {len(tasks)}  subsets: {[s for s, _ in items_by_subset]}  "
          f"limit: {args.limit or 'all'}  workers: {args.workers}  dry_run: {args.dry_run}")

    client = None if args.dry_run else make_client(cfg)
    fn = evaluate_mc_item if task == "mc" else evaluate_oq_item
    records: List[Dict[str, Any]] = []
    started = time.time()

    if args.workers > 1 and not args.dry_run:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fn, *t, cfg, client, args.dry_run): t[:2] for t in tasks}
            for future in as_completed(futures):
                rec = future.result()
                records.append(rec)
                _log(task, rec)
    else:
        for task_item in tasks:
            rec = fn(*task_item, cfg, client, args.dry_run)
            records.append(rec)
            _log(task, rec)

    records.sort(key=lambda r: (subsets.index(r["subset"]) if r["subset"] in subsets else 99,
                                r["index"]))
    selected = [s for s, _ in items_by_subset]
    print(f"{task.upper()} finished in {round(time.time() - started, 1)}s")
    return records, selected


def _log(task: str, rec: Dict[str, Any]) -> None:
    tag = f"{rec['subset']}#{rec['index']}"
    if rec.get("error"):
        print(f"  [error] {tag}: {rec['error']}")
    elif rec.get("dry_run"):
        print(f"  [dry]   {tag} images={rec.get('num_images')}")
    elif task == "mc":
        print(f"  [{'OK ' if rec.get('correct') else 'MISS'}] {tag} gold={rec['gold']} "
              f"pred={rec['predicted'] or '?'} imgs={rec.get('num_images')} "
              f"{rec.get('latency')}s")
    else:
        pred = (rec.get("predicted") or "").replace("\n", " ")[:60]
        print(f"  [{'OK ' if rec.get('correct') else 'MISS'}] {tag} "
              f"judge={rec.get('judge')} imgs={rec.get('num_images')} pred={pred!r}")


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def print_report(summary: Dict[str, Any], model: str, limit: int) -> None:
    print("\n" + "=" * 78)
    print(f"VisDocBench mini-bench — {model}  (LIMIT={limit or 'all'})")
    print("=" * 78)
    header = f"{'subset':24s} {'MC acc':>9s} {'MC n':>8s} {'OQ acc':>9s} {'OQ n':>8s}"
    print(header)
    mc = summary.get("mc", {})
    oq = summary.get("oq", {})
    subsets = list(dict.fromkeys(list(mc.get("by_subset", {})) + list(oq.get("by_subset", {}))))
    for subset in subsets:
        m = mc.get("by_subset", {}).get(subset)
        o = oq.get("by_subset", {}).get(subset)
        mc_acc = f"{m['accuracy']:.3f}" if m else "—"
        mc_n = f"{m['correct']}/{m['total']}" if m else "—"
        oq_acc = f"{o['accuracy']:.3f}" if o else "—"
        oq_n = f"{o['correct']}/{o['total']}" if o else "—"
        print(f"{subset:24s} {mc_acc:>9s} {mc_n:>8s} {oq_acc:>9s} {oq_n:>8s}")
    print("-" * 78)
    for name, part in (("MC", mc), ("OQ", oq)):
        overall = part.get("overall")
        if overall:
            print(f"{name:24s} {overall['accuracy']:>9.3f} "
                  f"{overall['correct']}/{overall['total']}  errors={overall['errors']}")
    print()


def write_markdown(summary: Dict[str, Any], model: str, limit: int, path: str) -> None:
    mc, oq = summary.get("mc", {}), summary.get("oq", {})
    subsets = list(dict.fromkeys(list(mc.get("by_subset", {})) + list(oq.get("by_subset", {}))))
    lines = [f"# VisDocBench mini-bench — {model}", "",
             f"LIMIT = {limit or 'all'} (per subset)", "",
             "| subset | MC acc | MC correct/total | OQ acc | OQ correct/total |",
             "|---|---|---|---|---|"]
    for subset in subsets:
        m = mc.get("by_subset", {}).get(subset)
        o = oq.get("by_subset", {}).get(subset)
        lines.append(
            f"| {subset} | {m['accuracy']:.3f} | {m['correct']}/{m['total']} |" if m else
            f"| {subset} | — | — |", 
        )
        if o:
            lines[-1] += f" {o['accuracy']:.3f} | {o['correct']}/{o['total']} |"
        else:
            lines[-1] += " — | — |"
    for name, part in (("MC", mc), ("OQ", oq)):
        overall = part.get("overall")
        if not overall:
            continue
        if name == "MC":
            lines.append(f"| **MC OVERALL** | **{overall['accuracy']:.3f}** | "
                         f"**{overall['correct']}/{overall['total']}** | — | — |")
        else:
            lines.append(f"| **OQ OVERALL** | — | — | **{overall['accuracy']:.3f}** | "
                         f"**{overall['correct']}/{overall['total']}** |")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"saved: {path}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="VisDocBench mini-bench evaluation")
    parser.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    parser.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    parser.add_argument("--output", default=os.path.join(HERE, "results", "results.json"))
    parser.add_argument("--limit", type=int, default=None,
                        help="items per subset; 0 = all (default: $LIMIT or 0)")
    parser.add_argument("--task", choices=["all", "mc", "oq"], default="all")
    parser.add_argument("--subsets", default="", help="comma separated subset names")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true",
                        help="render pages and build prompts without calling the API")
    args = parser.parse_args()

    if args.limit is None:
        try:
            args.limit = int(os.getenv("LIMIT", "0"))
        except ValueError:
            parser.error("LIMIT must be an integer")

    cfg = load_config(args.config)
    check_config(cfg, require_api=not args.dry_run)
    model_name = cfg["api"]["model"] or "dry-run"

    print(f"model: {model_name}")
    print(f"dataset: VisDocBench-parsed/  (root resolved)")
    print(f"data: {os.path.relpath(args.data_dir, HERE)}  output: {os.path.relpath(args.output, HERE)}")

    started = time.time()
    summary: Dict[str, Any] = {}
    predictions: Dict[str, List[Dict[str, Any]]] = {}

    if args.task in ("all", "mc"):
        predictions["mc"], mc_subsets = run_task("mc", cfg, args)
        summary["mc"] = summarize(predictions["mc"], mc_subsets)
    if args.task in ("all", "oq"):
        predictions["oq"], oq_subsets = run_task("oq", cfg, args)
        summary["oq"] = summarize(predictions["oq"], oq_subsets)

    payload = {
        "meta": {
            "benchmark": "VisDocBench mini-bench",
            "model": model_name,
            "limit_per_subset": args.limit,
            "workers": args.workers,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": round(time.time() - started, 1),
            "dry_run": args.dry_run,
            "render": cfg["render"],
        },
        "summary": summary,
        "predictions": predictions,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"\nsaved: {args.output}")

    print_report(summary, model_name, args.limit)
    md_path = os.path.splitext(args.output)[0] + "_summary.md"
    write_markdown(summary, model_name, args.limit, md_path)


if __name__ == "__main__":
    main()
