# Figure provenance

## `pathogen_topk_comparison.svg` and `.png`

| Item | Record |
|---|---|
| Purpose | Visualize the aggregate internal exploratory Top-K counts in `aggregated_results.csv` |
| Source data | User-provided aggregate counts; no case-level or physician-identifying data included; counts cannot be independently reproduced from this repository |
| Independent cases | 50 |
| Physician reading design | Three physicians each read the same 50 cases; 150 physician-case judgments per condition |
| Created | 2026-08-16, v0.1.0 public competition research release |
| SVG SHA-256 | `1498c2bc5ab183f0125ee6548771e6a6b292d1de32d7b844d7c1aef4d8018d66` |
| PNG SHA-256 | `8d0c9955af2d2c3383bd8efe5af5a57d68d5cf804375043112d9352e1679f074` |
| Hidden metadata scan | No author, creator, download-source, local path, API-key, MRN, or patient marker found |
| Public status | Approved for the public competition research release; descriptive-use limitations apply |

The figure uses direct labels, percentages and denominators in addition to color. The Top-3 panel is marked only as a descriptive visual focus, not as a prespecified primary endpoint. The five-LLM comparator takes the highest aggregate value separately at each Top-K and is not necessarily one model's curve. All differences are descriptive only. The repository lacks the paired case-level predictions, truth-adjudication record and complete evaluation protocol needed to reproduce the counts or test superiority; see `BENCHMARK_CARD.md`.
