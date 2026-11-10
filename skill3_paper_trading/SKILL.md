---
name: quant-paper-trading
description: 可转债模拟盘执行与复盘。仓位、止盈止损、移动止盈、邮件通知全部配置化；支持信号即时触发（不等定时汇总），输出逐笔流水与 Markdown 复盘报告。
version: 1.0.0
license: MIT
python: ">=3.9"
network: optional
tags: [quant, paper-trading, risk-control, smtp, review]
---

# Skill 3 · 模拟盘执行与复盘

## 1. 这个 Skill 做什么

读 Skill 2 的 Top3 信号 → 按配置建仓 → 按当前价判定止盈/止损/移动止盈/T+0 尾盘平仓 → 生成逐笔流水与复盘报告，并在**每笔成交时即时通知**。

## 2. 依赖安装

```bash
python3 -m pip install -r requirements.txt   # 无强制依赖，可跳过
```

核心仅标准库（`smtplib` / `email` / `json` / `csv`）。只有 `notify.channel = webhook` 才需要 `requests`。

## 3. 配置方法

```bash
cp config.example.json config.json   # 可选
cp .env.example .env                 # 只有需要真实发邮件时才填
```

| 分组 | 关键项 | 默认 |
| --- | --- | --- |
| `account` | `initial_capital` / `position_pct` / `max_positions` / `lot_size` / `commission_rate` | 50000 / 0.20 / 3 / 10 / 0.0003 |
| `exit_rules` | `take_profit_pct` / `stop_loss_pct` / `trailing_stop_pct` / `t0_close_at_eod` | +10.1% / −5% / 3% / true |
| `notify` | `enabled` / `instant_trigger` / `channel` / `smtp` / `webhook_url` | true / true / stdout |
| `paths` | `account_state` / `trade_log` / `review_dir` | `data/` 下 |
| `schedule` | 参考时间节点 10:00 / 13:30 选债、14:40 确认、15:30 复盘 | — |

**密钥安全**：QQ 邮箱授权码等只写 `.env`（已被 `.gitignore` 排除），`config.json` / `.env.example` 只有 `${SMTP_PASSWORD}` 占位符。检测到占位符时自动降级为 dry-run 打印，**不会报错也不会误发**。

> `instant_trigger: true` 来自环境踩坑：云沙箱休眠会杀掉 crontab，等到 15:30 定时汇总再发通知，信号可能永远发不出去。所以改为「产生即发送」。

## 4. 运行方式（Python）

```bash
python3 scripts/paper_engine.py --mode status
python3 scripts/paper_engine.py --mode buy
python3 scripts/paper_engine.py --mode review
python3 scripts/paper_engine.py --mode review --prices 123151=164.93,113050=130.00
python3 scripts/paper_engine.py --mode reset
```

## 5. 输入 / 输出契约

**输入**：`input.signals_csv`（Skill 2 的 `scored_signals.csv`）+ `input.review_prices_csv`（`code,current_price`）。主输入缺失时自动回落自带样例。

**输出**：`paper_account.json`（账户状态）、`trade_log.csv`（逐笔流水）、`reviews/review_YYYY-MM-DD.md`（可读复盘）。

---

## 6. ★ 纯文本 Prompt 模板（给无法运行 Python 的 AI）

> 把整段连同信号数据发给豆包 / Claude，它不需要运行代码即可模拟建仓、判定平仓并写出复盘。

```text
【开始】
你现在扮演「可转债 T+0 模拟盘执行器」。请不要运行代码，严格按下面的配置与规则手工推演，
输出建仓表、平仓表与复盘报告。

【一、账户与规则配置】
初始资金 50000.00 元；单标的仓位比例 0.20；最多同时持仓 3 只；
最小交易单位 1 手 = 10 张；手续费率 0.0003（买卖双向各收一次，金额四舍五入到分）。
止盈 +10.10%；止损 -5.00%；移动止盈回撤 3.00%；T+0 尾盘无条件平仓 = 开启。

【二、建仓规则（按信号排名依次处理）】
1. 单标的预算 budget = 当前可用现金 × 0.20
2. 手数 lots = floor(budget / (price × 10))；张数 shares = lots × 10
3. 若 shares <= 0 则跳过（资金不足 1 手）
4. 买入金额 amount = shares × price；买入手续费 fee_in = round(amount × 0.0003, 2)
5. 现金 -= amount + fee_in
6. 持仓数达到 3 只后停止。
（注意：每买一只后现金减少，但预算始终按「初始现金」计算，见演算示例）

【三、平仓判定优先级（自上而下，命中即停）】
P1 止盈：(现价/成本 - 1)×100 >= +10.10 → 原因「止盈 +10.10%」
P2 止损：(现价/成本 - 1)×100 <= -5.00 → 原因「止损 -5.00%」
P3 移动止盈：仅当「现价 > 成本」（已有浮盈）且「从持仓期最高价回撤 <= -3.00%」时生效
   → 原因「移动止盈 回撤 X%」。浮亏状态不得触发本条。
P4 T+0 尾盘平仓：以上都不命中 → 原因「T+0 尾盘平仓」

【四、盈亏计算】
卖出金额 = 现价 × shares；卖出手续费 fee_out = round(卖出金额 × 0.0003, 2)
盈亏 pnl = 卖出金额 - 成本 × shares - fee_out - fee_in
盈亏率 = pnl / (成本 × shares) × 100（保留 2 位小数）
现金 += 卖出金额 - fee_out

【五、演算示例（请照此展示过程）】
Top3 信号：123151 锦鸡转债 price=149.80 / 128136 立昂转债 price=141.20 / 113050 南银转债 price=128.60
预算 = 50000 × 0.20 = 10000.00
123151: lots=floor(10000/1498.00)=6 → 60张，amount=8988.00，fee_in=2.70
128136: lots=floor(10000/1412.00)=7 → 70张，amount=9884.00，fee_in=2.97
113050: lots=floor(10000/1286.00)=7 → 70张，amount=9002.00，fee_in=2.70
建仓后现金 = 50000 - 8990.70 - 9886.97 - 9004.70 = 22117.63

复盘现价：123151=164.93 / 128136=135.10 / 113050=128.60
123151: 涨幅 (164.93/149.80-1)×100 = +10.10% → 命中 P1 止盈
        pnl = 164.93×60 - 149.80×60 - 2.97 - 2.70 = +902.13 (+10.04%)
128136: 涨幅 -4.32%，未到止损、且处于浮亏不触发 P3 → 命中 P4 T+0 尾盘平仓，pnl = -432.81
113050: 涨幅 0.00% → 命中 P4，pnl = -5.40（仅亏双边手续费）
平仓后现金 = 50463.92，总资产 50463.92，累计 +0.93%，已实现盈亏 +463.92，胜率 1/3。

【六、输出格式（四段）】
第 1 段：建仓明细表（代码|名称|价格|张数|金额|买入手续费），并给出建仓后现金
第 2 段：平仓明细表（代码|名称|成本|现价|张数|盈亏|盈亏率|平仓原因），逐只说明命中了哪条优先级
第 3 段：账户总览（初始资金|可用现金|总资产|累计收益率|已实现盈亏|剩余持仓）
第 4 段：一句话复盘（当日盈亏主因、胜率、是否触发风控）

【七、约束】
- 不得跳过硬约束；不得把浮亏误判为「移动止盈」。
- 金额保留 2 位小数，盈亏率保留 2 位小数。
- 不得编造我未给出的现价；缺现价时按「未平仓，继续持有」处理并说明。
- 现在请对我下面给出的信号与现价执行推演：
【结束】
```

---

## 7. 跨平台说明

| 平台 | 用法 |
| --- | --- |
| WorkBuddy / 本地 / CI | `python3 scripts/paper_engine.py --mode buy/review` |
| GitHub 开源 | 本目录自包含；`stdout` 通道零配置即可跑通全流程 |
| 豆包 / Claude Web | 复制上方 Prompt 模板 + 贴入信号/现价 → 得到完整复盘 |
| 生产环境 | 把 `notify.channel` 改 `smtp` 并在 `.env` 填授权码；建议配合根目录 `scripts/keepalive.sh` 防休眠 |

## 8. 注意事项 & 免责声明

- 本 Skill 只做**模拟盘**，不连接任何券商接口、不下真实订单。
- `review_prices_csv` 是演示用价格；实盘请替换为实时行情导出。
- 仅供策略研究，**不构成投资建议**。
