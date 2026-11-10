# Skill 2 · 五维打分引擎

把可转债 T+0 的五维打分**权重、阈值、分段表全部外置到 `weights.json`**，改参数不用改代码。

## 快速上手

```bash
python3 -m pip install -r requirements.txt   # 无强制依赖，可跳过
cp config.example.json config.json            # 可选
python3 scripts/score.py                      # 直接跑：自带样例数据，零配置

# 接 Skill 1 的产出
python3 scripts/score.py --input ../skill1_data_pipeline/data/cb_snapshot.csv

# 调参
python3 scripts/score.py --weights my_weights.json --top 5
```

## 调参只需改 `weights.json`

| 配置项 | 作用 |
| --- | --- |
| `hard_filters` | 硬过滤：价格 100-150、正股动量 ≥3%、量比 ≥1.5 |
| `factors[].weight` | 五维权重（之和必须为 1.0，代码会校验） |
| `factors[].bands` | 分段映射表 `[min, max, score]`，`null` = 无穷 |
| `normalization.method` | `absolute_bands`（默认，逐行独立打分）或 `minmax`（横截面归一化） |
| `top_n` | 只推送 Top3 信号 |

公式：`score = 100 × Σ(weight_i × factor_score_i)`

## 为什么用 `absolute_bands`

`minmax` 需要当日全样本才能算，AI 只拿到几行数据就无法复现。`absolute_bands` **逐行独立打分**，Python 跑出来的结果和纯文本 Prompt 让豆包手算的结果**完全一致** —— 这是跨 AI 兼容的关键。

## 输出

- `data/scored_signals.csv`：Top3 信号，含总分与各因子分（`f_xxx` 列，便于人工核对）
- `data/scored_signals.json`：含 `meta`（权重版本 / 归一化方式 / 淘汰数量）与 `rejected` 明细

给 Skill 3 使用时，把 `skill3_paper_trading/config.json` 的 `input.signals_csv` 指向本文件输出即可。

---

仅供策略研究，不构成投资建议。
