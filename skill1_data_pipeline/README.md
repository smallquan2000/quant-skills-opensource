# Skill 1 · 量化数据管道

采集可转债行情快照，归一化为**全项目统一的标准 schema**，落 SQLite 并导出 CSV，供 Skill 2/3/4 消费。

## 快速上手

```bash
# 在本 Skill 目录内执行
python3 -m pip install -r requirements.txt
cp config.example.json config.json      # 可选：不复制也能直接跑（自动回落示例配置）
cp .env.example .env                    # 可选

# 离线演示（零网络、零配置）
python3 scripts/fetch_cb_data.py --provider csv --export-csv data/cb_snapshot.csv --dry-run

# 正式落库
python3 scripts/fetch_cb_data.py --provider csv --export-csv data/cb_snapshot.csv

# 接东方财富实时接口（原项目 b:MK0354 板块）
python3 scripts/fetch_cb_data.py --provider eastmoney --date 2026-11-10

# 只跑指定标的池
python3 scripts/fetch_cb_data.py --universe 113050,123138,128136
```

## 参数化三要素

| 要素 | 配置 | 命令行覆盖 |
| --- | --- | --- |
| 标的池 | `universe.mode` / `universe.codes` | `--universe code1,code2` |
| 日期 | `date` / `RUN_DATE` | `--date YYYY-MM-DD` |
| 数据库路径 | `paths.db` | `--db data/xxx.db` |

所有路径都相对**本 Skill 根目录**解析，与你在哪个目录敲命令无关。

## 标准数据契约（10 列）

`date, code, name, price, cb_change_pct, stock_change_pct, cb_stock_gap, volume_ratio, premium_rate, amount`

- `cb_stock_gap = stock_change_pct - cb_change_pct`（债股涨幅缺口）
- 上游接口缺列时按 `fallbacks` 回填；脏值（`-`、空、非数字）统一转 `NULL`
- SQLite 表 `cb_snapshot`，主键 `(date, code)`，重复执行 `INSERT OR REPLACE` 幂等

## 数据源

| provider | 说明 | 联网 |
| --- | --- | --- |
| `csv` | 标准 CSV 契约，跨 AI 通用（推荐给豆包 / Claude） | 否 |
| `eastmoney` | 东方财富 `push2delay` 接口，板块 `b:MK0354` | 是 |
| `westock` | WorkBuddy 环境由 AI 侧用 `westock` CLI 取数 → 写成标准 CSV → 用 `csv` 入库 | AI 侧 |

> 本脚本**不解析** `westock` CLI 的文本输出；统一以标准 CSV 作为交换格式，避免格式耦合。

## 交易日历

`scripts/trading_calendar.py` 提供 `is_trading_day()` / `prev_trading_day()`，周末与 `calendar.holidays` 自动跳过，T-1 跨节假日自动找最近交易日。非交易日执行会打印 `[SKIP]` 并返回 0（不报错），调试可加 `--force`。

## 输出

- `data/cb_market.db`（SQLite）
- `data/cb_snapshot.csv`（供 Skill 2 直接读取）

---

仅供策略研究，不构成投资建议。数据来源以交易所官方为准。
