---
name: quant-scoring-engine
description: 可转债 T+0 五维打分引擎。权重、阈值、分段表全部外置于 weights.json，代码零硬编码；采用逐行独立的 absolute_bands 归一化，使 Python 与纯文本 AI 手算结果完全一致。
version: 1.0.0
license: MIT
python: ">=3.9"
network: none
tags: [quant, convertible-bond, scoring, factor-model, prompt-reproducible]
---

# Skill 2 · 五维打分引擎

## 1. 这个 Skill 做什么

对可转债快照做**硬过滤 + 五维加权打分**，输出 Top3 候选信号。所有业务参数（权重、阈值、分段表、TopN）都在 `weights.json`，Python 代码里没有任何写死的策略常数。

## 2. 依赖安装

```bash
python3 -m pip install -r requirements.txt   # 无强制依赖，可跳过
```

纯 Python 3.9+ 标准库实现（`csv` / `json` / `argparse`），完全离线。

## 3. 配置方法

```bash
cp config.example.json config.json   # 可选
python3 scripts/score.py              # 自带样例数据，零配置即可跑
```

| 文件 | 作用 |
| --- | --- |
| `weights.json` | **唯一策略源**：`hard_filters`、`factors[].weight`、`factors[].bands`、`normalization.method`、`top_n` |
| `config.json` | 只管 IO：`weights_file`、`input`（csv/sqlite）、`output`、`top_n_override` |

**公式**：`score = 100 × Σ(weight_i × factor_score_i)`，权重之和必须为 1.0（代码会校验）。

**为什么默认 `absolute_bands`**：`minmax` 需要当日全样本才能算，AI 只拿到几行数据无法复现；`absolute_bands` **逐行独立打分**，Python 与纯文本手算结果完全一致，这是跨 AI 兼容的关键。

## 4. 运行方式（Python）

```bash
python3 scripts/score.py
python3 scripts/score.py --input ../skill1_data_pipeline/data/cb_snapshot.csv
python3 scripts/score.py --weights my_weights.json --top 5 --output data/my_signals.csv
```

## 5. 输入 / 输出契约

**输入**：Skill 1 的标准 10 列 CSV，或 `cb_snapshot` SQLite 表。主输入不存在时自动回落 `data/sample_signals.csv`。

**输出**：
- `data/scored_signals.csv` — Top3，含 `score` 与 `f_<factor>` 各因子分列（便于人工 / AI 核对）
- `data/scored_signals.json` — 含 `meta`（权重版本、归一化方式、淘汰数）与 `rejected` 淘汰明细

---

## 6. ★ 纯文本 Prompt 模板（给无法运行 Python 的 AI）

> **使用方法**：把下面整段连同你的行情数据发给豆包 / Claude。模型无需运行代码，只要照着分段表逐行查表、加权求和，就能得到与 Python **完全一致**的分数（已用 `absolute_bands` 保证可复现）。

```text
【开始】
你现在扮演「可转债 T+0 五维打分引擎」。请不要运行代码，严格用下面的规则逐行查表、手工计算，输出排序后的 Top3 信号。

【一、输入数据列】
date, code, name, price, cb_change_pct, stock_change_pct, cb_stock_gap, volume_ratio, premium_rate, amount
（单位为百分点，4.8 表示 +4.8%）

【二、第一步：硬过滤（三条必须同时满足，否则淘汰）】
F1  100 <= price <= 150
F2  stock_change_pct >= 3.0
F3  volume_ratio >= 1.5
被淘汰的记录要列出代码、名称与违反的过滤条件。

【三、第二步：五维分段打分表】
规则：区间为 min <= x < max；null 表示无穷。命中区间即得该档分数（0~1）。

维度 1「价格位置」column=price，权重 0.15
  x < 110 → 1.00 | 110<=x<125 → 0.85 | 125<=x<140 → 0.60 | 140<=x<150 → 0.35 | x>=150 → 0.00
维度 2「正股动量」column=stock_change_pct，权重 0.25
  x < 0 → 0.00 | 0<=x<3 → 0.30 | 3<=x<5 → 0.70 | 5<=x<8 → 0.90 | x>=8 → 1.00
维度 3「债股涨幅缺口」column=cb_stock_gap，权重 0.25
  x < 0 → 0.00 | 0<=x<1 → 0.35 | 1<=x<2 → 0.60 | 2<=x<4 → 0.85 | x>=4 → 1.00
维度 4「量比」column=volume_ratio，权重 0.20
  x < 1.0 → 0.00 | 1.0<=x<1.5 → 0.30 | 1.5<=x<2.0 → 0.60 | 2.0<=x<3.0 → 0.85 | x>=3.0 → 1.00
维度 5「转股溢价率」column=premium_rate，权重 0.15
  x < 5 → 1.00 | 5<=x<10 → 0.85 | 10<=x<20 → 0.60 | 20<=x<30 → 0.35 | x>=30 → 0.10

【四、第三步：加权求和】
score = 100 × (0.15×S1 + 0.25×S2 + 0.25×S3 + 0.20×S4 + 0.15×S5)
保留 2 位小数。按 score 降序排列；同分时按 code 升序。只输出前 3 名作为信号。

【五、演算示例（请照此格式展示你的计算过程）】
记录：123151 锦鸡转债 price=149.80, stock_change_pct=7.50, cb_stock_gap=4.10,
      volume_ratio=3.20, premium_rate=9.80
硬过滤：149.80 在[100,150]✓  7.50>=3✓  3.20>=1.5✓ → 通过
S1: 149.80 落在 140<=x<150 → 0.35
S2: 7.50   落在 5<=x<8    → 0.90
S3: 4.10   落在 x>=4      → 1.00
S4: 3.20   落在 x>=3.0    → 1.00
S5: 9.80   落在 5<=x<10   → 0.85
score = 100 × (0.15×0.35 + 0.25×0.90 + 0.25×1.00 + 0.20×1.00 + 0.15×0.85)
      = 100 × (0.0525 + 0.2250 + 0.2500 + 0.2000 + 0.1275) = 85.50

【六、输出格式（三段）】
第 1 段：被淘汰清单（代码 | 名称 | 违反的条件）
第 2 段：全部通过记录的打分明细表
  列：排名 | 代码 | 名称 | S1 | S2 | S3 | S4 | S5 | 总分
  并在表下给出 Top1 的逐项乘法展开（像第五段那样）
第 3 段：Top3 信号结论（代码 + 名称 + 总分 + 一句话理由）

【七、约束】
- 严格按分段表取值，不要自行插值、不要用 min-max 归一化、不要改动权重。
- 不得编造输入数据中不存在的标的。
- 分数保留 2 位小数；同分按代码升序。
- 现在请对我下面给出的真实数据执行打分：
【结束】
```

---

## 7. 跨平台说明

| 平台 | 用法 |
| --- | --- |
| WorkBuddy / 本地 / CI | `python3 scripts/score.py`，权重改 `weights.json` 即可 |
| GitHub 开源 | 本目录自包含，可单独 clone |
| 豆包 / Claude Web | 复制上方 Prompt 模板 + 贴入数据 → 得到与 Python 相同的分数 |
| 其它 AI Agent | 读 `weights.json` 的 `bands` 字段即可重建同一套打分逻辑 |

调参时请**同时**更新 `weights.json` 和本文档第六段的表格，避免 Python 与纯文本模板不一致。

## 8. 注意事项 & 免责声明

- `hard_filters` 会先于打分执行，因此样本量少时可能不足 3 只入选。
- 本模型为策略研究工具，历史表现不代表未来收益，**不构成投资建议**。
