# Benchmarks

本目录只存放可公开的聚合结果与图。它不包含病例文本、逐例答案、医生身份、MIMIC/DR.ECC 数据或模型原始响应。

**解读边界：**本目录能复核汇总百分比的算术，但不能逐例重现这些命中数。任何高低差异都只能作为内部描述性信号，不能宣称已证明 OwlPath 优于医生或其他 LLM。

阅读顺序：

1. [`BENCHMARK_CARD.md`](BENCHMARK_CARD.md)：分母、结果、限制和后续验证；
2. [`aggregated_results.csv`](aggregated_results.csv)：机器可读的聚合计数；
3. `figures/`：用于 README 或演示的聚合图。
4. [`FIGURE_PROVENANCE.md`](FIGURE_PROVENANCE.md)：图的数据来源、分母、哈希和发布门。

任何对数字、分母或方法的修改，必须同时更新 CSV、Benchmark Card 和图，并保留版本记录。
