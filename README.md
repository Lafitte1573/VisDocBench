# VisDocBench

VisDocBench 是一个面向文档理解的多模态评测基准，覆盖图表分析、跨页段落、
公式、单跳/多跳问答与 needle-in-a-haystack 等 9 类任务。模型以文档页面图像
为输入，需要在页面中定位证据并作答。

- **31,028 条**标注样本，**947 个**文档
- 选择题（MC）31,028 条；开放问答（OQ）15,350 条（6 个带参考答案的 QA 子集）
- 单一评测入口：`mini-bench/evaluate.py`（及 `run_eval.sh`），`LIMIT` 控制每子集样本数
- 仓库**不包含原始 PDF**：只发布标注、文档来源清单与下载脚本

> `mini-bench/` 是完整 benchmark 的**可选 300 条子集**，用于冒烟测试与快速比较；
> 它并不是 VisDocBench 的全部。全量评测使用 `release/annotations/`。

## 仓库结构

```
VisDocBench/
├── release/                 # lite 发布包（标注 + 来源清单 + 工具，不含 PDF）
│   ├── annotations/         # 完整标注（31,028 条）
│   ├── manifest.json        # 947 个文档的 source / source_id / URL / target_path
│   ├── manual_sources.csv   # 165 个需人工提供的文档
│   ├── download_data.py     # 按来源下载原始 PDF
│   ├── source_catalog.py
│   ├── verify_release.py
│   └── SHA256SUMS
├── mini-bench/              # 可选 300 条子集 + 评测脚本
│   ├── evaluate.py
│   ├── run_eval.sh
│   ├── data/                # 300 条子集（9 子集 × 20，seed=42）
│   ├── assets/              # 可选：mini 子集的源 PDF 缓存
│   └── README.md
├── scripts/                 # 发布与下载工具源码
└── VisDocBench-parsed/      # 本地文档数据（不随仓库发布）
```

## 获取数据与文档

- **标注 / 来源清单（ModelScope）**：
  <https://www.modelscope.cn/datasets/Pay4Begger/VisDocBench-lite>
  包含 9 个标注 JSON、`manifest.json`、`manual_sources.csv` 与下载/校验脚本。
- 仓库不直接再分发原始 PDF；`manifest.json` 记录每个文档的来源，
  `download_data.py` 从公开来源获取并放到评测期望的位置：

```bash
# 全量文档（947 个，写入当前目录的 VisDocBench-parsed/）
python release/download_data.py --root .

# 只要 mini-bench 子集所需的 364 个文档（写入 mini-bench/assets/）
python release/download_data.py --assets --mini

# 预览，不下载
python release/download_data.py --root . --dry-run
```

自动适配 arXiv、Google Patents、Project Gutenberg、Zenodo；交易所公告、
统计局稿件、本地课件等 165 个文档没有稳定公开 PDF 地址，会写入
`manual_sources.csv`，需按 `target_path` 手动放置。

## 全量评测

```bash
cd mini-bench

# 全量 31,028 条（LIMIT=0；耗时较长，建议先用 --subsets / LIMIT 验证链路）
LIMIT=0 python evaluate.py --data-dir ../release/annotations --workers 4

# 或每个子集前 100 条
LIMIT=100 python evaluate.py --data-dir ../release/annotations --workers 4
```

`evaluate.py` 同时产出 MC 与 OQ 结果，写出 `results/<name>.json` 与
同名的 `_summary.md`。`LIMIT` 是**每个子集**的样本数，不是总数。

## mini-bench 子集（可选）

300 条样本：9 个子集 × 20 条（`seed=42`），MC 180 / OQ 120。适合冒烟测试和
不同模型的快速对比，见 [mini-bench/README.md](mini-bench/README.md)。

```bash
cd mini-bench
LIMIT=1 ./run_eval.sh        # 冒烟：每子集 1 条
./run_eval.sh                # 子集全跑（300 条）
```

## 结果口径

- **MC**：模型输出 `**答案：X**`，与 `correct_index` 比对。
- **OQ**：模型简短作答后由同一模型做语义等价判定（self-judge），同时记录 token-F1。
- QA/needle 的证据页检索 query 包含参考答案，属于 oracle 证据定位；检索不是被评测能力。
- `LIMIT`、`render.max_pages_*` 会改变绝对分数，横向比较时需保持一致。

## 许可

标注元数据供研究使用；原始文档的版权与许可归各自权利人所有，本仓库不授予
再分发权。使用或再分发文档前请确认来源许可，详见 `release/manifest.json`
与 `release/README.md`。
