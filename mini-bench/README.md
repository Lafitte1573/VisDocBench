# VisDocBench mini-bench

> 本目录是 VisDocBench 的**可选 300 条子集**；完整 benchmark（31,028 条）与
> 项目整体结构见[项目 README](../README.md)。完整标注与来源清单：
> <https://www.modelscope.cn/datasets/Pay4Begger/VisDocBench-lite>。

VisDocBench mini-bench 是 **VisDocBench 的可选轻量评测子集**：从完整 benchmark 中
按子集抽取 300 条样本，用于快速验证与横向比较。完整 VisDocBench 共 **31,028 条**
标注，同一个评测入口也支持全量运行（见「全量评测」）。

覆盖图表、跨页段落、公式、单跳/多跳问答与 needle-in-a-haystack 等 9 类文档理解
任务。模型以文档页面图像为输入，需要在页面中定位证据并作答。

- **mini-bench 子集**：300 条（9 子集 × 20，`seed=42`；MC 180 / OQ 120）
- **完整 VisDocBench**：31,028 条（MC 31,028 / OQ 15,350），通过 `release/annotations` 运行
- **单一入口**：`run_eval.sh` 封装 `evaluate.py`，`LIMIT` 控制每个子集的样本数
- **无需训练**：只需一个兼容 OpenAI Chat Completions 的多模态模型服务
- **不含原始 PDF**：只发布代码与标注，源文档按 lite 发布包的 manifest 获取

> 运行前需准备文档：完整 `VisDocBench-parsed/`（947 个文档），或只准备 mini-bench
> 子集所需的 364 个文档（327 个可自动下载，37 个需人工提供）。

## 目录结构

```
VisDocBench/
├── VisDocBench-parsed/      # 本地数据（不随仓库发布）
├── mini-bench/
│   ├── README.md
│   ├── config.yaml          # 数据集根目录、API、渲染参数
│   ├── evaluate.py          # 唯一评测入口（MC + OQ）
│   ├── run_eval.sh          # 评测入口包装（LIMIT / WORKERS / TASK / OUTPUT）
│   ├── requirements.txt
│   ├── data/                # mini-bench 子集（300 条，9 个 JSON）
│   │   ├── _meta.json
│   │   └── *.json
│   ├── assets/              # 可选：download_data.py 生成的源 PDF 缓存
│   ├── results/             # 评测结果与汇总
│   └── .gitignore
├── release/                 # lite 发布包（build_release.py 生成）
│   ├── annotations/         # 完整标注（31,028 条）
│   ├── manifest.json
│   ├── manual_sources.csv
│   ├── download_data.py
│   ├── source_catalog.py
│   ├── verify_release.py
│   └── SHA256SUMS
└── scripts/
    ├── build_release.py     # 生成 lite 发布包
    ├── download_data.py     # 从来源下载 PDF
    ├── source_catalog.py    # 来源识别规则
    └── verify_release.py    # 发布包校验
```

## 环境准备

```bash
python -m pip install -r mini-bench/requirements.txt   # 在项目根目录执行
```

依赖：`openai`（任意 OpenAI 兼容服务）、`PyMuPDF`（PDF 渲染）、`PyYAML`（配置）。

### 1. 准备数据集

`VisDocBench-parsed/` 必须位于 **本项目根目录**（与 `mini-bench/` 同级）：

```
VisDocBench/
├── VisDocBench-parsed/
└── mini-bench/
```

如果数据放在其他位置，用环境变量指定项目根目录：

```bash
export VISDOCBENCH_ROOT=/path/to/project_root   # 该目录下应有 VisDocBench-parsed/
```

#### 数据从哪里来

VisDocBench 不随仓库分发原始 PDF，lite 发布包只包含：

```
annotations/        9 个标准化标注 JSON（31,028 条样本）
manifest.json       947 个文档的来源（source id / URL / 目标路径）
manual_sources.csv  165 个无稳定公开 PDF 地址的文档，需人工提供
download_data.py    从公开来源下载原始 PDF
verify_release.py   校验发布包
```

按评测范围准备文档：

```bash
# A. 只跑 mini-bench 子集：364 个文档 → mini-bench/assets/
python /path/to/release/download_data.py --assets --mini

# B. 跑完整 VisDocBench：947 个文档 → 项目根目录
python /path/to/release/download_data.py --root /path/to/project_root
```

完整 benchmark 的 947 个文档中 782 个有自动下载适配器、165 个需人工提供；
mini-bench 子集的 364 个文档中 327 个有适配器、37 个需人工提供（部分站点可能
限制自动下载，以 `download_report.json` 为准）。已有完整
`VisDocBench-parsed/` 时可直接跳过下载。

下载脚本自动支持 arXiv、Google Patents、Project Gutenberg、Zenodo；
交易所公告、统计局稿件、本地课件等没有稳定公开 PDF 地址的来源会写入
`manual_sources.csv`，需要按 `target_path` 手动放置。

脚本按「`mini-bench/assets/VisDocBench-parsed/` → `VISDOCBENCH_ROOT/VisDocBench-parsed/`」
的顺序查找 PDF，因此只下载 mini-bench 资产时无需完整数据集。assets 目录可用
`VISDOCBENCH_ASSETS` 覆盖。

### 3. 可选：构建 lite 发布包

发布包只含标注、来源清单和工具，不含 PDF：

```bash
# 在项目根目录执行
python scripts/build_release.py --version 1.0   # 生成 release/
python scripts/verify_release.py --release release
```

`manifest.json` 为每个文档记录 `source` / `source_id` / `landing_url` /
`pdf_url` / `target_path`；`download_data.py` 读取该清单并按来源下载。
`build_release.py --hash-pdfs` 可额外记录本地 PDF 的 SHA256（更慢）。

### 2. 配置模型服务

`config.yaml` 的 `api` 段默认为空，推荐用环境变量提供，避免把私有地址写进仓库：

```bash
export OPENAI_BASE_URL="https://<your-endpoint>/v1"
export OPENAI_API_KEY="<your-key>"
export OPENAI_MODEL="<your-multimodal-model>"
```

也可以直接修改 `config.yaml` 的 `api` 段。模型需支持图像输入
（`image_url`，base64 data URL）与 Chat Completions 协议。

## 运行

以下命令在 `mini-bench/` 内执行（`run_eval.sh` 会自动切换到脚本所在目录）。
`LIMIT` 控制**每个子集**的样本数，`LIMIT=0` 表示该数据目录下全部样本。

### mini-bench 子集（300 条）

```bash
# 冒烟测试：每个子集 1 条（9 条 MC + 6 条 OQ）
LIMIT=1 ./run_eval.sh

# 小规模评测：每个子集 5 条，4 并发
LIMIT=5 WORKERS=4 OUTPUT=results/results_5.json ./run_eval.sh

# 子集全跑：每子集 20 条，共 300 条
./run_eval.sh
```

### 全量评测（31,028 条）

`--data-dir` 指向 lite 发布包中的完整标注：

```bash
# 全量：LIMIT=0
./run_eval.sh --data-dir ../release/annotations --workers 4 --output results/full_results.json

# 或每个子集前 100 条
./run_eval.sh --data-dir ../release/annotations --limit 100

# 等价地，直接调用 Python
python evaluate.py --data-dir ../release/annotations --limit 0 --workers 4
```

全量运行耗时较长（约 31k 次多模态请求），建议先用 `LIMIT=1` 或 `--subsets` 验证链路。

`run_eval.sh` 读取 `LIMIT`（默认 0=全部）、`WORKERS`（默认 1）、`TASK`
（`all|mc|oq`）、`OUTPUT`，并会自动加载同目录下未提交的 `.env` 文件。

也可以用命令行参数：`python evaluate.py --limit 5`。

常用选项：

| 选项 | 说明 |
|---|---|
| `--limit N` | 每子集样本数，0=全部；未指定时读环境变量 `LIMIT` |
| `--task all\|mc\|oq` | 只跑选择题 / 只跑开放问答 / 两者都跑（默认） |
| `--subsets a,b` | 只评测指定子集 |
| `--workers N` | API 并发数（默认 1） |
| `--dry-run` | 只渲染页面、生成 prompt，不调用模型 |
| `--data-dir` / `--output` | 数据目录 / 结果路径 |

示例：只跑图表与公式两个子集，先检查页面与 prompt：

```bash
python evaluate.py --subsets chart_analysis,interline_equation --dry-run --limit 2
```

## 数据格式

`data/*.json` 均为 JSON 数组，每条样本至少包含：

```jsonc
{
  "file_key": "2603.06572",                          // 文档 ID
  "file_path": "VisDocBench-parsed/.../xxx.md",      // 相对路径，禁止机器绝对路径
  // 或 "source_files": ["VisDocBench-parsed/...", ...]  // 多文档 QA
  "question": "...",                                  // QA 类
  "answer": "...",                                    // OQ 参考答案（MC 子集可无）
  "choices": ["...", "...", "...", "..."],            // MC 选项
  "correct_index": 0                                  // MC 正确选项下标
}
```

不同子集另有 `masked_text` / `values` / `pre_text` / `post_text` / `formula` /
`bbox` / `page` 等任务字段。路径统一以 `VisDocBench-parsed/` 开头，评测时
拼到 `dataset_root` 下，并把 `.md`、`_middle.json`、`_content_bbox_list.json`
映射为同目录的 `_origin.pdf`。

## 评测协议

每条样本先定位证据页并渲染成图片，再连同题面发送给模型：

| 子集 | MC 题面 | 证据页 |
|---|---|---|
| `chart_analysis` | 文段中 `[MASK]` 数值 → 4 选 1 | 标注页，bbox 涂白并标 `Paragraph 1` |
| `interline_equation` | 还原 `Equation 1` 公式 → 4 选 1 | 标注页，bbox 涂白并标 `Equation 1` |
| `cross_page_blocks` | 判断给定文段的后续内容 → 4 选 1 | 文档抽样 12 页并按固定种子打乱 |
| 6 个 QA 子集 | 问题 + 4 选项 | BM25 检索证据页（每题 ≤ 6 页） |

- **MC**：模型输出 `**答案：X**`，与 `correct_index` 比对，指标为 accuracy。
- **OQ**：6 个带 `answer` 的 QA 子集；模型简短作答后，用同一模型做语义等价判定
  （1/0），指标为 judge accuracy，同时记录 token-F1。
- 参数集中在 `config.yaml` 的 `render` 段：`dpi`、`max_pages_total`、
  `max_pages_per_doc`、`max_doc_pages`。

### 结果口径说明

- QA/needle 的页面检索 query 由 `question + answer` 构成，属于 **oracle 证据定位**；
  needle 子集优先用 `source_quote` 精确匹配。检索不是被评测的能力。
- `cross_page_blocks` 只抽样固定页数并打乱，页码顺序推理在抽样范围内进行。
- OQ 的 judge 与作答使用同一模型（self-judge）。需要更严格结论时，可替换更强的
  judge 模型或人工抽检。
- 改变 `LIMIT`、`render.max_pages_*` 会改变绝对分数；横向比较时请保持配置一致。

## 输出

`evaluate.py` 写出两个文件（`--output` 指定 JSON 路径，Markdown 自动同名前缀）：

```jsonc
// results/results.json
{
  "meta": { "benchmark": "...", "model": "...", "limit_per_subset": 0,
            "workers": 4, "created_at": "...", "elapsed_sec": 355.0,
            "dry_run": false, "render": { ... } },
  "summary": {
    "mc": { "overall": {...}, "by_subset": { "chart_analysis": {...}, ... } },
    "oq": { "overall": {...}, "by_subset": { ... } }
  },
  "predictions": {
    "mc": [ { "subset": "...", "index": 0, "gold": "D", "predicted": "D",
              "correct": true, "raw_output": "...", "latency": 2.1 } ],
    "oq": [ { "subset": "...", "gold": "...", "predicted": "...",
              "judge": 1, "f1": 0.8, ... } ]
  }
}
```

同时打印并保存 Markdown 汇总表（`results/results_summary.md`）。

## 参考结果

`LIMIT=1` 冒烟（每子集 1 条，仅验证链路）：

| 任务 | 结果 |
|---|---|
| MC | 9/9（1.000） |
| OQ | 5/6（0.833） |

`LIMIT=20`（mini-bench 子集 300 条，`--workers 4`，约 249s）：

| subset | MC acc | MC correct/total | OQ acc | OQ correct/total |
|---|---|---|---|---|
| chart_analysis | 0.750 | 15/20 | — | — |
| cross_page_blocks | 1.000 | 20/20 | — | — |
| interline_equation | 0.950 | 19/20 | — | — |
| mm_multi_hop_qa | 0.850 | 17/20 | 0.700 | 14/20 |
| mm_single_hop_qa | 0.900 | 18/20 | 0.500 | 10/20 |
| multi_hop_qa | 0.850 | 17/20 | 0.600 | 12/20 |
| needle_in_a_haystack | 1.000 | 20/20 | 0.800 | 16/20 |
| single_hop_qa | 0.800 | 16/20 | 0.700 | 14/20 |
| single_hop_qa_mm | 0.800 | 16/20 | 0.750 | 15/20 |
| **OVERALL** | **0.878** | **158/180** | **0.675** | **81/120** |

> 表中模型为 DeepSeek-V4.1-Flash-1017，仅作链路与量级参考。分数会随模型、
> 推理参数与 `render` 配置变化。

## 数据来源与许可

- mini-bench 的 300 条样本是完整 VisDocBench（31,028 条标注）的可选子集，使用
  固定随机种子抽取，样本已随仓库提供；完整标注位于 `release/annotations/`。
- VisDocBench 不直接再分发原始 PDF；lite 发布包只含标注与
  `manifest.json` 中的来源记录。原始文档的版权与使用条款归各自权利人所有，
  下载与使用前请确认符合对应来源的许可。
- `manifest.json` 为每个文档记录 `source`、`source_id`、`landing_url`、
  `pdf_url`（若存在）与目标路径，可直接用于溯源与人工核对。
