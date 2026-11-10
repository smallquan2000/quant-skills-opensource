# 环境踩坑与运维经验

> 这一节是从「本地跑通 → 云沙箱 7×24 定时运行」过程中踩过的坑抽象出来的**平台无关**经验。
> 所有脚本已按这些经验做了防护，本文说明「为什么这么设计」。

---

## 1. 为什么不能完全依赖系统 cron

**现象**：云 IDE / 沙箱在闲置一段时间后进入休眠，`cron` 进程被**直接杀掉**，之后所有定时任务静默失效，第二天才发现没跑。已观察到多次复发。

### 两个都无效的方案

| 方案 | 为什么无效 |
| --- | --- |
| 定时发 heartbeat 请求「保活」 | 实测无法阻止沙箱的休眠策略 |
| 用 crontab 每分钟调 `keepalive.sh` 把 cron 拉起来 | **逻辑死锁**：`keepalive.sh` 自身也是由 cron 调用的。cron 一死，就没有任何东西会去调用它 —— 守护者与被守护者处在同一故障域，「自我拉起」根本不会发生 |

> ⚠️ 第二条是本项目早期版本的设计倒退，现已修正。结论很明确：**调度必须有一个运行在沙箱故障域之外的触发源**。`scripts/keepalive.sh` 现在明确标注「仅方案 A 使用」。

### 结论：按部署环境二选一

| | **方案 A：VPS / 本地服务器** | **方案 B：云沙箱 / Cloud Studio** |
| --- | --- | --- |
| 前提 | cron 常驻，不会被杀 | 平台支持「外部 HTTP 请求唤醒沙箱」+ Web 服务托管 |
| 调度器 | 系统 cron | `scripts/embedded_scheduler.py`（Flask + `threading.Timer`） |
| 触发源 | 本机 crontab | 沙箱外的定时器（cron-job.org 等）GET `/wakeup` |
| 休眠后 | 不受影响 | 外部请求唤醒 → 服务重启 → `tick()` 幂等补跑当天任务 |
| 守护脚本 | 可选 `keepalive.sh` | **不要用**（死锁） |
| 依赖 | 无 | `flask`（`--tick-once` 模式则零依赖） |

---

#### 方案 A：VPS / 本地服务器 —— 直接用 cron

cron 在这类环境里是稳定的，直接用 crontab 即可，可选挂一个每分钟的 `keepalive.sh` 做进程自检：

```cron
0  8  * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill1_data_pipeline/scripts/fetch_cb_data.py --provider csv
0 10  * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill2_scoring_engine/scripts/score.py
30 13 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill2_scoring_engine/scripts/score.py
40 14 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill3_paper_trading/scripts/paper_engine.py --mode buy
30 15 * * 1-5  cd <REPO_DIR> && <PYTHON_BIN> skill3_paper_trading/scripts/paper_engine.py --mode review
*  *  * * *    REPO_DIR=<REPO_DIR> PYTHON_BIN=<PYTHON_BIN> bash <REPO_DIR>/scripts/keepalive.sh
```

`<...>` 换成你自己的实际值，不要提交真实路径。crontab 是零额外开销的本地调度，不消耗任何 AI 词元。

---

#### 方案 B：云沙箱 —— 内嵌调度 + 外部唤醒（推荐，生产验证）

**架构**：

```
[沙箱外] cron-job.org / GitHub Actions   每 5~10 分钟
        │  GET /wakeup?token=...（不受沙箱休眠影响，只负责「敲门」）
        ▼
[沙箱内] Flask 服务  scripts/embedded_scheduler.py
        ├── /health   状态探针
        ├── /wakeup ──┐
        └── threading.Timer（进程存活时每 60s）─┐
                                              ▼
                                   tick()  ← 同一个入口，threading.Lock 串行
                                     │  读 data/scheduler_state.json
                                     │  「今天该跑但没跑」→ 立即补跑
                                     ▼
                          subprocess 调 Skill 1/2/3（cwd=各 Skill 目录）
```

**为什么能根治「第二天静默失效」**：

1. **触发源在沙箱外**：cron-job.org 运行在别处，沙箱休眠影响不到它。
2. **HTTP 请求会唤醒沙箱**：平台的 Web 服务/端口转发机制收到请求后把沙箱拉起，Flask 进程随之恢复。
3. **幂等补跑**：`tick()` 用状态文件记录「某任务某天已跑」。即使睡过头错过了 10:00，只要 10:05 被唤醒，它看到 `select_bond_am` 已到点且当天无记录 → 立即补跑。不会重复，也不会静默跳过。
4. **解释器用 `sys.executable`**：调度器用「当前这个带依赖的解释器」调子脚本，从根本上绕开 §3 的裸环境踩坑。
5. **进程内 Timer 是双保险**：只要进程活着，`threading.Timer` 每 60s 也会触发同一个 `tick()`，不依赖外部敲门也能按时执行。

**部署步骤**：

```bash
# 1. 装依赖（仅方案 B 需要）
python3 -m pip install -r requirements.txt

# 2. 配置（可选，不复制会自动回落示例配置）
cp scripts/scheduler.example.json scripts/scheduler.json

# 3. 设置唤醒令牌（公网必须；不要写进 scheduler.json）
export WAKEUP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

# 4. 由平台的 Web 服务机制托管启动（不要用 nohup ... &，那样休眠时一样被杀）
python3 scripts/embedded_scheduler.py
#    监听 0.0.0.0:8080，提供 /health 与 /wakeup

# 5. 在 cron-job.org 建一个每 5~10 分钟的任务，URL 填：
#    https://<你的沙箱公网地址>/wakeup?token=<WAKEUP_TOKEN>
#    请求方法 GET；超时设 30s 即可（/wakeup 默认异步返回 202）
```

手动验证：

```bash
curl "http://127.0.0.1:8080/health"
curl "http://127.0.0.1:8080/wakeup?sync=1&token=$WAKEUP_TOKEN"   # sync=1 便于调试看结果
python3 scripts/embedded_scheduler.py --tick-once                  # 不启 Flask，纯标准库跑一次
```

**前提条件（务必确认）**：平台必须支持「外部 HTTP 请求能唤醒休眠沙箱」且 Web 服务由平台托管常驻。若平台不提供该能力，改用 GitHub Actions 之类**沙箱外**的定时器直接调用 `--tick-once`（纯标准库、不需要 Flask），或干脆迁到 VPS 走方案 A。

**安全**：`WAKEUP_TOKEN` 只在环境变量 / `.env` 中设置，配合 `?token=` 或 `X-Wakeup-Token` 请求头；未设置时 `/health` 会显示 `"auth": "open"`，仅适合内网。不要把这个 token 提交进仓库。

**状态与日志**：`data/scheduler_state.json`（幂等记录，自动只留最近 7 天）、`data/scheduler.log`（超过 1000 行自动滚动），均已被 `.gitignore` 排除。

---

## 2. 通知要「即时触发」，不要等定时汇总

**现象**：原本设计 15:30 统一汇总发邮件。一旦沙箱在盘中休眠，信号产生后到汇总前进程被杀，通知永远发不出去。

**修正**：`notify.instant_trigger = true` —— 每笔建仓 / 平仓**产生即发送**，不等定时汇总。汇总报告作为补充，不作为唯一通道。

Skill 3 的 `notifier.py` 还支持：
- `stdout`（默认，零配置、零网络，日志即可留痕）
- `smtp`（QQ 邮箱等，用**授权码**而非登录密码）
- `webhook`（企业微信 / 钉钉 / 自定义）

**占位符检测**：密钥为空或仍是 `YOUR_...` 时自动降级 dry-run，只打印不发送 —— 保证 clone 后无需配置也不会崩，更不会误发到示例邮箱。

---

## 3. Python 版本冲突：crontab 必须显式指定解释器

**现象**：机器上同时存在多个 Python：某个 pyenv 环境装了 pandas/requests，而系统 `/usr/bin/python3` 是裸环境。crontab 的 PATH 极简，直接用 `python3` 会命中裸环境，`ModuleNotFoundError` 导致任务静默失败。

**规则**：
- crontab 里一律用 `${PYTHON_BIN}` / 完整路径显式指定**带依赖的那个解释器**
- 不要依赖 `source ~/.zshrc` 或 PATH 推导（非交互 shell 不加载）
- 本项目 `scripts/run_all.sh` / `keepalive.sh` 都支持 `PYTHON_BIN` 环境变量注入
- **方案 B 无需操心这一项**：`embedded_scheduler.py` 用 `sys.executable` 调子脚本，自动复用「当前这个带依赖的解释器」，从根上绕开裸环境问题
- 用 `python3 scripts/healthcheck.py` 定期自检模块可用性

```cron
0 10 * * 1-5 cd <REPO_DIR> && <PYTHON_BIN> skill2_scoring_engine/scripts/score.py
```

---

## 4. 非交易日与跨节假日 T-1

**现象**：节假日 / 周末跑定时任务会拿到空数据或上一交易日残留数据，导致信号错乱；「T-1 日」直接用 `date - 1` 在长假后会落到休市日。

**规则**：
- 周末 + 可配置 `calendar.holidays` 双重判断
- 非交易日**跳过并返回 0**（不算失败，避免告警风暴），日志明确打印 `[SKIP]` 与最近交易日
- T-1 用 `prev_trading_day()` 逐日回溯（默认最多 15 天）找到最近交易日
- 节假日清单按当年交易所公告维护，放在 `config.json` 而非写死在代码里

Skill 1 的 `trading_calendar.py` 已实现，调试时可加 `--force` 强制执行。

---

## 5. 路径与配置

- **绝不硬编码绝对路径**。所有路径由 `config_loader.py` 相对各自 Skill 根目录解析，与调用时的 cwd 无关 —— 这是能跨平台 / 跨 AI / 上传 GitHub 的前提。
- **真实配置不进仓库**：`config.json` / `.env` 已在 `.gitignore` 中排除，仓库里只留 `*.example.*`。
- **示例数据要提交、运行时产物不提交**：`sample_*.csv` 保留，`*.db` / `*_signals.csv` / `reviews/` 排除。
- 推送前跑一次自查：`git diff --cached | grep -i -E "password|token|smtp_|api_key"`。

---

## 6. 自检

`scripts/healthcheck.py`（零第三方依赖）检查：

| 项 | 失败级别 |
| --- | --- |
| Python 版本 < 3.9、标准库缺失 | FAIL |
| `requests` 未安装（仅在线源需要） | WARN |
| 某 Skill 缺 `config.json`（回落示例） | WARN |
| 关键数据文件超过 N 小时未更新 | WARN |
| 指定进程（如 `cron`）未存活 —— 仅方案 A | WARN |

有 FAIL 时返回退出码 1，便于接入告警；WARN 不影响退出码。

**方案 B 请直接轮询调度服务自带的 `/health`**：它返回 `scheduler_running`、`timer_alive`、`seconds_since_last_tick`、`today_completed`、`pending_now`。把 `timer_alive=false` 或 `seconds_since_last_tick` 异常增大作为告警条件，比检查 cron 进程可靠得多。

---

## 7. 定时任务参考时间节点

| 时间 | 动作 | 对应脚本 |
| --- | --- | --- |
| 08:00 | 采集 / 选股 | Skill 1 |
| 10:00 | 选债 + 打分 | Skill 1 → Skill 2 |
| 13:30 | 下午场选债 + 打分 | Skill 1 → Skill 2 |
| 14:40 | 尾盘确认 / 建仓 | Skill 3 `--mode buy` |
| 15:30 | 盘后复盘 | Skill 3 `--mode review` |
| 每分钟 | **方案 A** 守护 + 自检 | `keepalive.sh` |
| 每 5~10 分钟（沙箱外） | **方案 B** 唤醒 | cron-job.org → `GET /wakeup` |
| 每 60 秒（进程内） | **方案 B** 日内定时 | `threading.Timer` → `tick()` |

这些只是参考值：方案 A 写在 crontab，方案 B 写在 `scripts/scheduler.json` 的 `tasks[].time`，都可按平台调整。两套方案都是零 AI 词元开销的本地 / 外部定时。

方案 B 的 `tick()` 是幂等补跑，所以**不必把外部唤醒频率卡到分钟级**：只要唤醒间隔短于你能容忍的最大延迟即可（例如 5 分钟），到点未跑的任务会在下一次唤醒时被补上。

---

## 8. 其它

- 通过 webview 代理访问看板类服务时，通常需要认证参数；这类参数属于环境配置，走环境变量，不要写进代码或提交。
- 长期运行的日志要滚动（`keepalive.sh` 已保留最近 500 行），否则会撑爆磁盘。
- 邮件通道建议先设 `channel: stdout` 跑通全流程，再切 `smtp`，便于定位问题。
