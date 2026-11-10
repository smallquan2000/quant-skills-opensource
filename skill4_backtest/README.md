# Skill 4 · 轻量级回测引擎

**100% 纯离线**：不联网、不读实时行情、零第三方依赖。只吃一份长表历史 CSV，逐日推演打分 → 选 TopN → 结算 → 输出净值与绩效。

## 快速上手

```bash
python3 -m pip install -r requirements.txt   # 全部是注释，可直接跳过
cp config.example.json config.json            # 可选
python3 scripts/make_sample_data.py           # 生成演示用合成数据（首次运行）
python3 scripts/backtest.py                   # 跑回测
```

自定义区间 / 参数：

```bash
python3 scripts/backtest.py --history data/my_history.csv \
  --start 2026-11-02 --end 2026-11-13 --top 3
```

## 输入 CSV 格式（长表）

每日每只转债一行，必须包含 `weights.json` 中各因子的 `column` 列，外加一个未来收益列：

```csv
date,code,name,price,cb_change_pct,stock_change_pct,cb_stock_gap,volume_ratio,premium_rate,amount,forward_return_pct
2026-11-02,113050,南银转债,128.60,1.20,4.80,3.60,2.10,8.50,385000000,2.35
```

`forward_return_pct` 单位为**百分点**（2.35 = +2.35%），代表该信号持有到平仓的实现收益。

> ⚠️ `data/sample_history.csv` 是固定随机种子生成的**合成演示数据**，不是真实行情，切勿据此做实盘判断。请替换为你自己的历史数据。

## 推演逻辑

1. 按交易日分组，先过 `hard_filters`
2. 用 `weights.json` 的 `absolute_bands` 打分（与 Skill 2 完全同一套规则）
3. 取 `top_n` 等权持有，组合日收益 = 入选标的 `forward_return_pct` 均值 − 双边手续费
4. 累计净值，逐日记录回撤

## 输出指标

累计收益 / 年化收益 / 最大回撤 / 日胜率 / 交易胜率 / 夏普比率 / 交易笔数 / 期末资金

- `data/equity_curve.csv`：净值曲线（含每日入选代码）
- `data/backtest_report.json`：完整结构化结果（含 `daily_picks`）
- `data/backtest_report.md`：可读报告

想与 Skill 2 共用单一权重源：把 `config.json` 的 `weights_file` 改为 `../skill2_scoring_engine/weights.json`。

---

仅供策略研究，不构成投资建议。
