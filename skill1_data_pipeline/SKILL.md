---
name: quant-data-pipeline
description: 可转债量化数据管道。参数化标的池、日期、数据库路径；采集行情并归一化为全项目统一标准 schema，落 SQLite 与 CSV。适用于需要可复现、可离线、可跨 AI 交换的数据底座。
version: 1.0.0
license: MIT
python: ">=3.9"
network: optional
tags: [quant, convertible-bond, data-pipeline, sqlite, cross-platform]
---

# Skill 1 · 量化数据管道

## 1. 这个 Skill 做什么

把异构的可转债行情数据源（离线 CSV / 东方财富 push2delay / WorkBuddy `westock` CLI 产出）统一清洗成 **10 列标准契约**，写入 SQLite 并导出 CSV，作为 Skill 2（打分）、Skill 3（模拟盘）、Skill 4（回测）的共同输入。

三要素全部参数化：**标的池**、**日期**、**数据库路径**。

## 2. 依赖安装

```bash
python3 -m pip install -r requirements.txt
```

只有 `eastmoney` provider 需要 `requests`；`csv` 通道零第三方依赖。全部路径相对本 Skill 根目录，与当前工作目录无关。

## 3. 配置方法

```bash
cp config.example.json config.json   # 可选：不复制也能跑，自动回落示例配置
cp .env.example .env                 # 可选：仅在需要凭据时填写
```

| 配置键 | 说明 | 命令行覆盖 |
| --- | --- | --- |
| `data_source.provider` | `csv` / `eastmoney` | `--provider` |
| `data_source.csv_path` | 离线输入 CSV | `--csv` |
| `data_source.eastmoney.board` | 板块代码，默认 `b:MK0354` | — |
| `universe.mode` / `universe.codes` | 标的池 `all` / `include` / `exclude` | `--universe` |
| `date` / `RUN_DATE` | 交易日，留空取当天 | `--date` |
| `paths.db` | SQLite 路径 | `--db` |
| `paths.export_csv` | 导出给下游的标准 CSV | `--export-csv` |
| `calendar.holidays` | 法定休市日清单（按当年交易所公告维护） | — |
| `fallbacks` | 上游缺字段时的回填值 | — |

**密钥规则**：真实凭据只放 `.env`（已被 `.gitignore` 排除），配置文件里只有 `${VAR}` 占位符。

## 4. 运行方式（Python）

```bash
python3 scripts/fetch_cb_data.py --provider csv --export-csv data/cb_snapshot.csv
python3 scripts/fetch_cb_data.py --provider eastmoney --date 2026-11-10
python3 scripts/fetch_cb_data.py --universe 113050,123138 --dry-run
python3 scripts/fetch_cb_data.py --db data/cb_market.db --force
```

`--dry-run` 只打印不落库。非交易日会自动 `[SKIP]` 并返回 0，不会让定时任务误报错。

## 5. 输入 / 输出契约

**标准 10 列**：`date, code, name, price, cb_change_pct, stock_change_pct, cb_stock_gap, volume_ratio, premium_rate, amount`

- `cb_stock_gap = stock_change_pct − cb_change_pct`
- 脏值（`-` / 空 / 非数字）→ `NULL`；缺列按 `fallbacks` 回填
- SQLite 表 `cb_snapshot`，主键 `(date, code)`，重复执行幂等

**跨 AI 交换约定**：无法运行 Python 的 AI（豆包、Claude Web 等）可用 `westock` 等工具取数后，**直接产出这份标准 CSV**，再由本管道用 `--provider csv` 入库。本脚本不解析任何 CLI 的文本输出，避免格式耦合。

---

## 6. ★ 纯文本 Prompt 模板（给无法运行 Python 的 AI）

> **使用方法**：把下面 `【开始】` 到 `【结束】` 之间的整段文字，连同你的原始数据一起发给豆包 / Claude / 任何大模型。它不需要运行代码，就能按同一套规则算出清洗结果。

```text
【开始】
你现在扮演「量化数据管道执行器」。请严格按以下规则处理我随后给出的原始可转债行情记录，不要运行代码，直接用推理完成清洗与判断，并按指定格式输出。

【一、标准输出契约（10 列，顺序固定）】
date, code, name, price, cb_change_pct, stock_change_pct, cb_stock_gap, volume_ratio, premium_rate, amount
含义：交易日, 转债代码, 简称, 转债价格(元), 转债涨幅(%), 正股涨幅(%), 债股涨幅缺口(%), 量比, 转股溢价率(%), 成交额(元)

【二、清洗规则（逐条严格执行）】
1. code 去除首尾空格；name 去除首尾空格。
2. 所有数值字段：若值为 "-"、"空"、非数字或缺失，视为「缺失」。
3. cb_stock_gap 若缺失或无法计算，按公式重算：
   cb_stock_gap = stock_change_pct - cb_change_pct（保留 2 位小数）
   若两者仍缺任一，则回填 0.0。
4. 其余缺失数值按以下 fallbacks 回填：
   stock_change_pct=0.0, premium_rate=0.0, volume_ratio=1.0, amount=0.0
5. 所有百分比字段单位统一为「百分点」（4.8 表示 +4.8%，不要写成 0.048）。
6. 标的池过滤（mode=all 时跳过本步）：
   - include：只保留 codes 白名单中的代码
   - exclude：剔除 codes 黑名单中的代码

【三、交易日判断规则】
1. 周六、周日为休市日。
2. 法定休市日清单 holidays（示例）：2026-01-01、2026-10-01、2026-10-02、2026-10-05
3. 若目标日期是休市日：输出一行「[SKIP] YYYY-MM-DD 非交易日，最近交易日 T-1 = YYYY-MM-DD」，
   并说明「本管道不执行」。T-1 需逐日向前回溯直到找到工作日且不在 holidays 中。
4. 若为交易日：正常输出清洗结果。

【四、输出格式（三段，缺一不可）】
第 1 段：交易日判定结论（一行）
第 2 段：标准 CSV 表格（Markdown 表格，列名严格为上述 10 列；金额用普通数字不要千分位）
第 3 段：统计（有效记录数 / 被剔除记录数 / 回填字段次数）

【五、示例输入】
date=2026-11-10, mode=include, codes=[113050, 123138]
date,code,name,price,cb_change_pct,stock_change_pct,cb_stock_gap,volume_ratio,premium_rate,amount
2026-11-10,113050, 南银转债 ,128.60,1.20,4.80,-,2.10,8.50,385000000
2026-11-10,123138,丝路转债,132.45,0.85,5.20,4.35,1.95,12.30,156000000
2026-11-10,999999,测试转债,160.00,1.00,2.00,1.00,-,-,-

【六、示例期望输出（请照此风格作答）】
第 1 段：2026-11-10 是周二，不在 holidays 中 → 交易日，执行。
第 2 段：
| date | code | name | price | cb_change_pct | stock_change_pct | cb_stock_gap | volume_ratio | premium_rate | amount |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-10 | 113050 | 南银转债 | 128.60 | 1.20 | 4.80 | 3.60 | 2.10 | 8.50 | 385000000 |
| 2026-11-10 | 123138 | 丝路转债 | 132.45 | 0.85 | 5.20 | 4.35 | 1.95 | 12.30 | 156000000 |
说明：113050 的 cb_stock_gap 原为 "-"，按 4.80-1.20=3.60 重算；名称首尾空格已去除；
      999999 因 mode=include 不在白名单被剔除。
第 3 段：有效记录 2 条，剔除 1 条，回填 1 次（cb_stock_gap 重算）。

【七、约束】
- 不得编造我没有给出的行情数据；缺失就按规则回填并明确标注。
- 不得改变列名与顺序。
- 现在请处理我下面给出的真实数据：
【结束】
```

---

## 7. 跨平台说明

| 平台 | 用法 |
| --- | --- |
| WorkBuddy / 本地 / CI | 直接运行 `python3 scripts/fetch_cb_data.py` |
| GitHub 开源 | 本目录可单独 clone 使用，不依赖其它 3 个 Skill |
| 豆包 / Claude Web（无 Python） | 复制上方 Prompt 模板，让 AI 产出标准 CSV |
| WorkBuddy `westock` 数据源 | AI 侧取数 → 写成标准 CSV → `--provider csv` 入库 |

## 8. 注意事项 & 免责声明

- `calendar.holidays` 为示例值，请按当年交易所公告维护。
- `eastmoney` 接口不提供正股涨幅与转股溢价率，需外部映射补齐，否则按 `fallbacks` 回填（会降低打分质量）。
- 数据可能有延迟，以交易所官方为准。本 Skill 仅做数据处理，**不构成投资建议**。
