# 架构设计

## 1. 设计原则

| 原则 | 具体做法 |
| --- | --- |
| **自包含** | 每个 Skill 有独立的 `SKILL.md` / `README.md` / `requirements.txt` / 配置示例 / `scripts/`，可单独 clone，互不 import |
| **零硬编码路径** | 所有路径相对各自 Skill 根目录解析（`Path(__file__).resolve().parent.parent`），与 cwd 无关；代码里不存在任何绝对路径 |
| **配置与代码分离** | 策略常数（权重、阈值、仓位、止盈止损）全在 JSON；IO 路径全在 `config.json`；密钥全在 `.env` |
| **可复现** | Skill 2/4 用 `absolute_bands` 逐行独立打分，Python 与纯文本 AI 手算结果完全一致 |
| **优雅降级** | `config.json` 缺失 → 回落示例配置；输入缺失 → 回落样例数据；密钥缺失 → 通知降级 dry-run |
| **离线优先** | 除 Skill 1 的 `eastmoney` 与 Skill 3 的 SMTP/webhook 外，全部零网络依赖 |

## 2. 公共模块为何复制 4 份

`scripts/config_loader.py` 在 4 个 Skill 里内容相同。这是**刻意设计**：把它抽成共享包会引入跨目录 import，破坏「单独下载一个 Skill 就能跑」的目标。代价是 4 份约 90 行的重复，收益是每个 Skill 完全自治。修改公共逻辑时请 4 份同步。

## 3. 统一数据契约

**标准 10 列**（Skill 1 输出，Skill 2/3 输入）：

| 列 | 含义 | 单位 |
| --- | --- | --- |
| `date` | 交易日 | YYYY-MM-DD |
| `code` / `name` | 转债代码 / 简称 | — |
| `price` | 转债价格 | 元 |
| `cb_change_pct` | 转债涨幅 | 百分点 |
| `stock_change_pct` | 正股涨幅 | 百分点 |
| `cb_stock_gap` | 债股涨幅缺口 = 正股涨幅 − 转债涨幅 | 百分点 |
| `volume_ratio` | 量比 | 倍 |
| `premium_rate` | 转股溢价率 | 百分点 |
| `amount` | 成交额 | 元 |

**回测长表**（Skill 4 输入）：上述列 + `forward_return_pct`（单位百分点）。

> 所有百分比统一用**百分点**（4.8 表示 +4.8%），不混用 0.048，这是跨 AI 推演不歧义的前提。

## 4. 各 Skill 职责

### Skill 1 · 数据管道
- 三要素参数化：`universe`（标的池）、`date`、`paths.db`
- 三种 provider：`csv`（离线标准契约）、`eastmoney`（`b:MK0354`）、`westock`（AI 侧取数→CSV→入库）
- 本脚本**不解析** `westock` CLI 的文本输出：AI 侧产出标准 CSV 即可，避免格式耦合
- `trading_calendar.py`：周末 + 可配置 `holidays` 休市判断，`prev_trading_day()` 支持跨节假日 T-1
- SQLite `cb_snapshot(date, code)` 主键幂等

### Skill 2 · 打分引擎
- `weights.json` 是**唯一策略源**：`hard_filters` / `factors[].weight` / `factors[].bands` / `top_n`
- 先硬过滤，再五维加权：`score = 100 × Σ(w_i × s_i)`
- 两种归一化：`absolute_bands`（默认，逐行独立）与 `minmax`（横截面）
- 输出 Top3 + 各因子分列 `f_<key>`，便于人工 / AI 核对

### Skill 3 · 模拟盘
- 状态机：`paper_account.json`（现金 / 持仓 / 已实现盈亏 / 净值曲线）
- 平仓优先级：止盈 → 止损 → 移动止盈（**仅浮盈**）→ T+0 尾盘平仓
- 通知 `instant_trigger`：每笔成交立即发送，不等定时汇总
- 通道 `stdout` / `smtp` / `webhook`，占位符检测自动 dry-run

### Skill 4 · 离线回测
- 逐日：过滤 → 打分 → TopN → 等权 `forward_return_pct` 均值 − 双边手续费 → 累计净值
- 防失真：交易日 < 60 时年化收益置空
- 自带 `weights.json` 副本保持独立；也可配置为指向 Skill 2 的文件实现单一权重源

## 5. 扩展点

| 想做的事 | 改哪里 |
| --- | --- |
| 换数据源 | Skill 1 新增一个 provider 函数 + `config.json` 的 `provider` |
| 调权重 / 加第 6 个因子 | `weights.json` 的 `factors`（权重和须为 1.0）+ 同步更新纯文本模板 |
| 换止盈止损规则 | Skill 3 `config.json` 的 `exit_rules` |
| 换通知渠道 | `notifier.py` 加一个 `_send_xxx()` |
| 换回测收益口径 | Skill 4 `config.json` 的 `forward_return_column` |
| 接真实实盘 | 复用 Skill 2 信号，替换 Skill 3 为券商接口（**本项目不含下单能力**） |

## 6. 目录约定

```
<skill_root>/
├── SKILL.md              AI 读：用途/依赖/配置/运行 + 纯文本 Prompt 模板
├── README.md             人类读：快速上手
├── requirements.txt      依赖（无强制依赖时全是注释）
├── config.example.json   IO 配置示例（只有占位符）
├── .env.example          密钥占位符
├── weights.json          仅 Skill 2/4：策略参数
├── scripts/              可执行代码
└── data/                 示例数据（提交）+ 运行时产物（不提交）
```

## 7. 调度层（部署相关，不属于 4 个 Skill）

调度不属于任何一个 Skill，统一放在仓库根 `scripts/`：

| 方案 | 文件 | 适用环境 |
| --- | --- | --- |
| A 系统 cron | `keepalive.sh` + crontab | VPS / 本地服务器 |
| B 内嵌调度 | `embedded_scheduler.py` + `scheduler.example.json` | 云沙箱 |

方案 B 的三个要点：

1. **故障域隔离**：触发源（cron-job.org 等）在沙箱之外，不受休眠影响；`keepalive.sh` 之所以不可用，是因为它自身也由 cron 调用，形成死锁。
2. **幂等补跑**：`tick()` 用 `scheduler_state.json` 记录「任务 × 日期」，被唤醒后发现「到点但当天未跑」即刻补执行，不会重复也不会静默跳过。
3. **单一入口**：`/wakeup` 与 `threading.Timer` 都调用同一个 `tick()`，`threading.Lock` 保证并发串行；`sys.executable` + `cwd=<skill 目录>` 调用子脚本，既避开 Python 版本冲突，也不破坏各 Skill 的相对路径语义。

详见 [ENVIRONMENT_NOTES.md](ENVIRONMENT_NOTES.md) §1。
