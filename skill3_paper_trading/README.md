# Skill 3 · 模拟盘执行与复盘

仓位、止盈止损、邮件通知**全部配置化**，支持「信号即时触发」与每日复盘报告。

## 快速上手

```bash
python3 -m pip install -r requirements.txt   # 无强制依赖，可跳过
cp config.example.json config.json            # 可选
cp .env.example .env                         # 需要真实发邮件时才填

python3 scripts/paper_engine.py --mode status   # 查看账户
python3 scripts/paper_engine.py --mode buy      # 读 Skill 2 的 Top3 信号建仓
python3 scripts/paper_engine.py --mode review  # 按当前价判定止盈/止损/T+0 平仓 + 出复盘
python3 scripts/paper_engine.py --mode reset   # 重置账户（流水保留）
```

## 配置项

| 分组 | 关键项 | 默认值 |
| --- | --- | --- |
| `account` | `initial_capital` / `position_pct` / `max_positions` / `lot_size` / `commission_rate` | 50000 / 0.20 / 3 / 10 / 0.0003 |
| `exit_rules` | `take_profit_pct` / `stop_loss_pct` / `trailing_stop_pct` / `t0_close_at_eod` | +10.1% / -5% / 3% / true |
| `notify` | `enabled` / `instant_trigger` / `channel` / `smtp` / `webhook_url` | true / true / stdout |
| `paths` | `account_state` / `trade_log` / `review_dir` | `data/` 下 |

## 三种通知通道

| channel | 行为 |
| --- | --- |
| `stdout` | 直接打印（默认，零配置、跨平台） |
| `smtp` | QQ 邮箱等；**未配置授权码时自动降级 dry-run**，不会报错 |
| `webhook` | POST JSON；未配置 URL 时自动降级 dry-run |

`instant_trigger: true` 表示每笔建仓/平仓**立即发送**，不等 15:30 定时汇总 —— 来自云沙箱休眠导致定时任务丢失的踩坑经验。

> **密钥安全**：真实 SMTP 授权码只放 `.env`（已被 `.gitignore` 排除）。`config.json` 和 `.env.example` 里只有 `${SMTP_PASSWORD}` 占位符。

## 输出

- `data/paper_account.json`：账户状态（现金、持仓、已实现盈亏、净值曲线）
- `data/trade_log.csv`：逐笔流水（含盈亏、收益率、平仓原因）
- `data/reviews/review_YYYY-MM-DD.md`：可读复盘（账户总览、当日平仓、胜率、剩余持仓）

---

仅供策略研究，不构成投资建议。
