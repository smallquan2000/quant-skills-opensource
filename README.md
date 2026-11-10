# Quant Skills OpenSource · 可转债 T+0 量化技能包

把一套跑通的本地可转债 T+0 量化系统，拆成 **4 个标准化、自包含、可跨平台复用的 Skill**。

设计目标：**同一个逻辑，既能被 Python 执行，也能被任何大模型用纯文本推演**。

- 🧩 **WorkBuddy**：直接当 Skill 用，或调用 `westock` 数据源
- 💻 **本地电脑 / CI**：`python3 scripts/xxx.py` 直接跑
- 🐙 **GitHub 开源**：每个 Skill 可单独 clone，互不依赖
- 🤖 **豆包 / Claude / 其它 AI**：把 `SKILL.md` 里的**纯文本 Prompt 模板**贴过去，无需运行 Python 也能模拟出同样结果

---

## 四个 Skill

| # | 目录 | 做什么 | 关键设计 | 联网 |
| --- | --- | --- | --- | --- |
| 1 | `skill1_data_pipeline` | 采集、清洗、落库 | 参数化标的池 / 日期 / DB 路径；可插拔数据源；交易日历 | 可选 |
| 2 | `skill2_scoring_engine` | 五维打分 → Top3 | **权重全在 `weights.json`**，代码零硬编码；逐行独立打分 | 否 |
| 3 | `skill3_paper_trading` | 模拟盘 + 复盘 | 仓位 / 止盈止损 / 邮件全配置化；信号即时触发 | 可选 |
| 4 | `skill4_backtest` | 离线回测 | 纯标准库、零网络；样本不足自动禁用年化 | 否 |

---

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/<YOUR_NAME>/quant-skills-opensource.git
cd quant-skills-opensource

# 2. 建议用虚拟环境（避免系统 Python 缺少依赖）
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
python3 -m pip install -r requirements.txt             # 装齐可选依赖：flask（方案 B）/ requests
# 只需跑 4 个 Skill 的话可跳过：它们的核心逻辑仅依赖 Python 标准库

# 3. 配置（可选，不配置也能直接跑演示模式）
cp skill1_data_pipeline/config.example.json skill1_data_pipeline/config.json
cp skill2_scoring_engine/config.example.json skill2_scoring_engine/config.json
cp skill3_paper_trading/config.example.json skill3_paper_trading/config.json
cp skill4_backtest/config.example.json skill4_backtest/config.json

# 4. 需要真实发邮件时才配置密钥
cp skill3_paper_trading/.env.example skill3_paper_trading/.env
# 编辑 .env，填入 SMTP 授权码；.env 已被 .gitignore 排除

# 5. 一键跑通全链路（全离线，零密钥）
bash scripts/run_all.sh
```

> `config.json` 不存在时会自动回落 `config.example.json`，所以**第 3、4 步都可以跳过**，clone 完直接执行 `run_all.sh` 就能看到完整结果。

### 手动分步执行

```bash
cd skill1_data_pipeline && python3 scripts/fetch_cb_data.py --provider csv --export-csv data/cb_snapshot.csv
cd ../skill2_scoring_engine && python3 scripts/score.py
cd ../skill3_paper_trading   && python3 scripts/paper_engine.py --mode reset && python3 scripts/paper_engine.py --mode buy && python3 scripts/paper_engine.py --mode review
cd ../skill4_backtest        && python3 scripts/make_sample_data.py && python3 scripts/backtest.py
```

---

## 数据流

```
[行情源: CSV / 东财 / westock]
        │
        ▼
Skill 1  标准 10 列 CSV + SQLite ──────┐
        │                              │
        ▼                              │
Skill 2  weights.json → Top3 信号      │
        │                              │
        ├──────────────► Skill 3  模拟盘建仓/复盘/通知
        │                              │
        └──────────────► Skill 4  离线回测（长表历史 CSV）
```

统一数据契约（10 列）：
`date, code, name, price, cb_change_pct, stock_change_pct, cb_stock_gap, volume_ratio, premium_rate, amount`

完整设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 配置与密钥安全

| 文件 | 是否提交 | 作用 |
| --- | --- | --- |
| `config.example.json` / `.env.example` | ✅ 提交 | 只有占位符，可安全公开 |
| `config.json` / `.env` | ❌ 排除 | 你的真实配置与密钥 |
| `data/sample_*.csv` | ✅ 提交 | 演示用合成数据 |
| `data/*.db`、运行时产物 | ❌ 排除 | 由脚本生成 |

密钥通过 `${VAR}` 占位符从 `.env` / 环境变量注入，例如 `${SMTP_PASSWORD}`。**未填写时邮件通道自动降级为 dry-run 打印，不会报错也不会误发**。

环境变量清单见 [`.env.example`](.env.example)。

---

## 跨平台与「不能跑 Python 的 AI」

每个 `SKILL.md` 的第 6 节都有一套**自包含的纯文本 Prompt 模板**：把整段文字连同数据发给豆包 / Claude，模型照着分段表手工查表、加权求和，就能得出与 Python **完全一致**的结果。

关键在 Skill 2 采用 `absolute_bands`（逐行独立打分）而非 `minmax`（需要全样本归一化）—— 后者 AI 只拿到几行数据时无法复现。

| 平台 | 怎么用 |
| --- | --- |
| WorkBuddy | 直接运行脚本；或用 `westock` 取数写成标准 CSV 再入库 |
| 本地 / CI | `python3 scripts/xxx.py` |
| 豆包 / Claude Web | 复制 `SKILL.md` 第 6 节 Prompt 模板 |
| 其它 AI Agent | 读 `weights.json` 的 `bands` 字段即可重建同一套逻辑 |

汇总索引见 [docs/PROMPT_TEMPLATES.md](docs/PROMPT_TEMPLATES.md)。

---

## 目录结构

```
quant_skills_opensource/
├── README.md · LICENSE · .gitignore · .env.example · requirements.txt
├── docs/
│   ├── ARCHITECTURE.md          4 个 Skill 的数据流与契约 + 调度层
│   ├── PROMPT_TEMPLATES.md      纯文本模板索引与跨 AI 指南
│   └── ENVIRONMENT_NOTES.md     两套调度方案 / 防休眠 / 自检 / 环境踩坑
├── scripts/
│   ├── run_all.sh               一键跑通全链路
│   ├── healthcheck.py           系统自检
│   ├── embedded_scheduler.py    ★ 方案 B：Flask + threading.Timer 内嵌调度
│   ├── scheduler.example.json   调度任务与唤醒配置示例
│   └── keepalive.sh             方案 A：cron 守护（云沙箱勿用）
├── skill1_data_pipeline/        SKILL.md · README.md · requirements.txt
│   ├── config.example.json · .env.example
│   ├── scripts/  config_loader.py · trading_calendar.py · fetch_cb_data.py
│   └── data/     cb_sample.csv（示例）→ 运行时产物
├── skill2_scoring_engine/       SKILL.md · README.md · requirements.txt
│   ├── weights.json             ★ 五维权重唯一来源
│   ├── config.example.json · .env.example
│   ├── scripts/  config_loader.py · score.py
│   └── data/     sample_signals.csv
├── skill3_paper_trading/        SKILL.md · README.md · requirements.txt
│   ├── config.example.json · .env.example
│   ├── scripts/  config_loader.py · paper_engine.py · notifier.py
│   └── data/     sample_signals.csv · sample_review_prices.csv
└── skill4_backtest/             SKILL.md · README.md · requirements.txt
    ├── weights.json             ★ 权重副本（保持独立可跑）
    ├── config.example.json · .env.example
    ├── scripts/  config_loader.py · backtest.py · make_sample_data.py
    └── data/     sample_history.csv（合成演示数据）
```

---

## 环境经验与两套调度方案（重要）

> ⚠️ **选错调度方案会让定时任务在第二天静默失效**。云沙箱休眠会直接杀掉系统 cron，
> 而「靠 cron 每分钟调 `keepalive.sh` 把 cron 拉起来」存在**逻辑死锁** —— cron 一死就没人调它了。
> 守护者与被守护者处在同一故障域，这个方案在云沙箱里不成立。详见 [docs/ENVIRONMENT_NOTES.md §1](docs/ENVIRONMENT_NOTES.md)。

| | **方案 A：VPS / 本地服务器** | **方案 B：云沙箱（生产验证）** |
| --- | --- | --- |
| 调度器 | 系统 cron | `scripts/embedded_scheduler.py`（Flask + `threading.Timer`） |
| 触发源 | 本机 crontab | 沙箱外定时器（cron-job.org）`GET /wakeup` |
| 休眠后 | 不受影响 | 外部请求唤醒 → `tick()` **幂等补跑**当天错过的任务 |
| 守护脚本 | 可选 `keepalive.sh` | **不要用**（死锁） |
| 依赖 | 无 | `flask`（`--tick-once` 模式零依赖） |

**方案 B 快速启动**：

```bash
python3 -m pip install -r requirements.txt
cp scripts/scheduler.example.json scripts/scheduler.json       # 可选，不复制会回落示例配置
export WAKEUP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
python3 scripts/embedded_scheduler.py                         # 由平台 Web 服务机制托管
# 在 cron-job.org 建「每 5~10 分钟 GET /wakeup?token=...」的外部定时器

curl "http://127.0.0.1:8080/health"                           # 状态探针
curl "http://127.0.0.1:8080/wakeup?sync=1&token=$WAKEUP_TOKEN"  # 手动触发（调试）
python3 scripts/embedded_scheduler.py --tick-once              # 不启 Flask 的兜底命令
```

> **前提**：平台需支持「外部 HTTP 请求唤醒休眠沙箱」并托管 Web 服务。不要用 `nohup python &`
> 自己后台跑（休眠时一样被杀）。平台不支持时，用 GitHub Actions 等沙箱外调度直接调
> `--tick-once`，或迁到 VPS 走方案 A。

**其它高频坑**（已抽象进 `scripts/` 与 [docs/ENVIRONMENT_NOTES.md](docs/ENVIRONMENT_NOTES.md)）：

1. **通知要即时触发**，不要等 15:30 定时汇总，否则休眠后信号永远发不出去
2. **Python 版本冲突**：方案 A 在 crontab 里用 `PYTHON_BIN`；方案 B 自动用 `sys.executable`
3. **非交易日 / 跨节假日 T-1** 自动跳过与回溯
4. **所有路径相对仓库 / Skill 根目录**，代码里没有任何硬编码绝对路径

<details>
<summary>方案 A 参考 crontab（仅 VPS / 本地服务器，云沙箱勿用）</summary>

```cron
0  8  * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill1_data_pipeline/scripts/fetch_cb_data.py --provider csv
0 10  * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill2_scoring_engine/scripts/score.py
30 13 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill2_scoring_engine/scripts/score.py
40 14 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill3_paper_trading/scripts/paper_engine.py --mode buy
30 15 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill3_paper_trading/scripts/paper_engine.py --mode review
*  *  * * *    REPO_DIR=<REPO_DIR> PYTHON_BIN=<PYTHON_BIN> bash <REPO_DIR>/scripts/keepalive.sh
```

`<...>` 换成你自己的实际值，不要提交真实路径。
</details>

---

## 维护者：推送到 GitHub

```bash
cd <REPO_DIR>
git init
git add .
git status                       # 确认没有 config.json / .env / *.db 被暂存
git commit -m "feat: 4 个可复用的可转债量化 Skill"

# 方式 A：gh CLI（推荐）
gh auth login
gh repo create quant-skills-opensource --public --source=. --remote=origin --push

# 方式 B：先在 GitHub 网页建空仓库，再手动关联
git remote add origin https://github.com/<YOUR_NAME>/quant-skills-opensource.git
git branch -M main
git push -u origin main
```

推送前建议执行 `git diff --cached | grep -i -E "password|token|smtp_|api_key"` 做一次密钥自查。

---

## 常见问题

**Q：不配置任何东西能跑吗？** 能。`config.json` 缺失自动回落示例配置，`stdout` 通知通道零密钥，`csv` 数据源零网络。

**Q：为什么示例回测里年化收益是 N/A？** 交易日少于 60 天时年化会指数级失真（10 天样本能算出数万 %），引擎主动置空。请用自己的长周期历史数据。

**Q：`sample_history.csv` 是真实行情吗？** 不是，是固定随机种子生成的合成演示数据，仅用于验证流程。

**Q：改了权重要注意什么？** 除改 `weights.json` 外，请同步更新 `skill2_scoring_engine/SKILL.md` 第 6 节纯文本模板里的分段表，保证 Python 与 AI 手算一致。

---

⚠️ **免责声明**：本项目为量化策略研究与工程实践示例，所有数据（尤其 `sample_*.csv`）均为演示用途，**不构成任何投资建议或个股推荐**。投资有风险，决策需谨慎。

**License**：MIT · 详见 [LICENSE](LICENSE)
