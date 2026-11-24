# assets/

`download_data.py` 可以把 mini-bench 需要的源 PDF 下载到这里：

```bash
python ../release/download_data.py --assets --mini
```

生成的 `VisDocBench-parsed/` 目录会被 `evaluate.py` 优先查找；`download_report.json`
和 `manual_sources.csv` 也写在此目录（均不提交到 Git）。
