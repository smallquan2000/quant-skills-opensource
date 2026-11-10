#!/usr/bin/env python3
"""内嵌调度引擎 —— 云沙箱环境下的 cron 替代方案（方案 B）。

## 为什么需要它
云沙箱休眠会直接杀掉系统 cron。而「靠 cron 每分钟调 keepalive.sh 把 cron 拉起来」
存在逻辑死锁：cron 一死，就没有任何东西会去调用 keepalive.sh，自愈根本不会发生。
守护者与被守护者处在同一故障域，这个方案在云沙箱里不成立。

## 本模块的解法
把调度逻辑搬进 Flask Web 进程，并把「触发源」放到沙箱之外：

    [外部定时器 cron-job.org]  --GET /wakeup-->  [本 Flask 服务]
                                                     │
                              ┌──────────────────────┴──────────────┐
                              ▼                                     ▼
                    threading.Timer（进程存活期间日内定时）    tick() 幂等补跑
                                                              │
                                                              ▼
                                                    subprocess 调各 Skill

- `/wakeup` 与 Timer 都调用**同一个** `tick()`；
- `tick()` 依据 `data/scheduler_state.json` 记录「某任务某天已跑」，到点未跑即补跑；
- 即使进程被冻结/重启，状态文件仍在 → 外部一敲门就能补上当天错过的任务；
- 外部定时器运行在沙箱之外，不受沙箱休眠影响，只负责「敲门」。

## 关键工程细节
- **零硬编码路径**：仓库根目录由 `__file__` 定位；任务、时间、时区、密钥全配置化。
- **Python 解释器用 `sys.executable`**：自动复用当前带依赖的解释器，根治
  「crontab 命中系统裸 Python 导致 ModuleNotFoundError」的踩坑。
- **可选 token 鉴权**：`WAKEUP_TOKEN` 防止公网 `/wakeup` 被任意触发。
- **幂等 + 锁**：`threading.Lock` 保证 Timer 与 /wakeup 并发时不重复执行。
- `--tick-once` 模式纯标准库，不需要 Flask，可作为外部兜底命令。

## 用法
    python3 -m pip install flask
    cp scripts/scheduler.example.json scripts/scheduler.json
    python3 scripts/embedded_scheduler.py                 # 启动服务 + 调度线程
    python3 scripts/embedded_scheduler.py --tick-once     # 只跑一次 tick（调试/兜底）

    curl "http://127.0.0.1:8080/health"
    curl "http://127.0.0.1:8080/wakeup?sync=1&token=$WAKEUP_TOKEN"

外部定时器配置：每 5~10 分钟 GET 一次 `/wakeup`（详见 docs/ENVIRONMENT_NOTES.md）。
"""
from __future__ import annotations

import argparse
import atexit
import hmac
import json
import os
import re
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 仓库根目录 = scripts/ 的上一级；所有相对路径以此为锚点，与 cwd 无关
ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.1.0"
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")

DEFAULT_TASKS: list[dict] = [
    {
        "id": "data_pipeline", "time": "08:00", "label": "采集 / 选股",
        "steps": [
            {"skill": "skill1_data_pipeline", "script": "scripts/fetch_cb_data.py",
             "args": ["--provider", "csv", "--export-csv", "data/cb_snapshot.csv"]},
        ],
    },
    {
        "id": "select_bond_am", "time": "10:00", "label": "上午场选债 + 打分",
        "steps": [
            {"skill": "skill1_data_pipeline", "script": "scripts/fetch_cb_data.py",
             "args": ["--provider", "csv", "--export-csv", "data/cb_snapshot.csv"]},
            {"skill": "skill2_scoring_engine", "script": "scripts/score.py", "args": []},
        ],
    },
    {
        "id": "select_bond_pm", "time": "13:30", "label": "下午场选债 + 打分",
        "steps": [
            {"skill": "skill1_data_pipeline", "script": "scripts/fetch_cb_data.py",
             "args": ["--provider", "csv", "--export-csv", "data/cb_snapshot.csv"]},
            {"skill": "skill2_scoring_engine", "script": "scripts/score.py", "args": []},
        ],
    },
    {
        "id": "confirm", "time": "14:40", "label": "尾盘确认 / 建仓",
        "steps": [
            {"skill": "skill3_paper_trading", "script": "scripts/paper_engine.py",
             "args": ["--mode", "buy"]},
        ],
    },
    {
        "id": "review", "time": "15:30", "label": "盘后复盘",
        "steps": [
            {"skill": "skill3_paper_trading", "script": "scripts/paper_engine.py",
             "args": ["--mode", "review"]},
        ],
    },
]


def _resolve(path_value: str) -> Path:
    p = Path(path_value).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def _expand(value: Any) -> Any:
    """与 4 个 Skill 的 config_loader 保持同一套 ${VAR:-default} 占位符语法。"""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            token = m.group(1)
            inner = m.group(0)[2:-1]
            default = inner.split(":-", 1)[1] if ":-" in inner else ""
            return os.environ.get(token) or default
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: str | None = None) -> dict:
    candidates = [path] if path else [os.environ.get("SCHEDULER_CONFIG"),
                                      "scripts/scheduler.json",
                                      "scripts/scheduler.example.json"]
    for c in candidates:
        if c and _resolve(c).exists():
            raw = json.loads(_resolve(c).read_text(encoding="utf-8"))
            cfg = _expand(raw)
            cfg["_source"] = str(_resolve(c))
            return cfg
    raise FileNotFoundError("找不到调度配置，请复制 scripts/scheduler.example.json")


def _make_tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        # 兜底：东八区，保证 A 股时间节点不出错
        return timezone(timedelta(hours=8))


class EmbeddedScheduler:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tz = _make_tz(cfg.get("timezone", "Asia/Shanghai"))
        self.python_bin = cfg.get("python_bin") or sys.executable
        self.tick_interval = int(cfg.get("tick_interval_seconds", 60))
        self.timeout = int(cfg.get("task_timeout_seconds", 300))
        self.catch_up_days = int(cfg.get("catch_up_days", 0))
        self.tasks = cfg.get("tasks") or DEFAULT_TASKS
        self.holidays = set(cfg.get("calendar", {}).get("holidays", []) or [])
        self.token = (cfg.get("wakeup_token") or "").strip()

        paths = cfg.get("paths", {})
        self.state_path = _resolve(paths.get("state", "data/scheduler_state.json"))
        self.log_path = _resolve(paths.get("log", "data/scheduler.log"))
        self.state_retention_days = int(cfg.get("state_retention_days", 7))
        for p in (self.state_path, self.log_path):
            p.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._timer: threading.Timer | None = None
        self._log_count = 0
        self._last_tick: datetime | None = None

    # ------------------------------------------------------------------ #
    # 日志与状态
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        line = f"{datetime.now(self.tz).isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._log_count += 1
        if self._log_count % 100 == 0:
            self._rotate_log()

    def _rotate_log(self) -> None:
        if not self.log_path.exists():
            return
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 1000:
            self.log_path.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._log("[WARN] 状态文件损坏，重置为空")
            return {}

    def _save_state(self, state: dict) -> None:
        # 只保留最近 N 天，避免长期运行无限增长
        cutoff = (datetime.now(self.tz).date() - timedelta(days=self.state_retention_days)).isoformat()
        state = {d: v for d, v in state.items() if d >= cutoff}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------------ #
    # 交易日（规则与 Skill 1 trading_calendar.py 一致）
    # ------------------------------------------------------------------ #
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d.isoformat() not in self.holidays

    # ------------------------------------------------------------------ #
    # 任务执行
    # ------------------------------------------------------------------ #
    def _run_step(self, step: dict) -> str:
        skill_dir = ROOT / step["skill"]
        script = skill_dir / step["script"]
        if not script.exists():
            raise FileNotFoundError(f"脚本不存在: {script}")
        cmd = [self.python_bin, str(script)] + [str(a) for a in step.get("args", [])]
        env = os.environ.copy()
        env["PYTHON_BIN"] = self.python_bin
        r = subprocess.run(cmd, cwd=str(skill_dir), env=env, capture_output=True,
                           text=True, timeout=self.timeout)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-400:]
            raise RuntimeError(f"exit={r.returncode} :: {tail}")
        return (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "ok"

    # ------------------------------------------------------------------ #
    # 核心：一次 tick = 检查所有「到点且当天未跑」的任务并补跑
    # ------------------------------------------------------------------ #
    def tick(self, force: bool = False) -> dict:
        with self._lock:
            now = datetime.now(self.tz)
            self._last_tick = now
            today = now.date()
            hhmm = now.strftime("%H:%M")
            state = self._load_state()
            executed: list[str] = []
            failed: list[dict] = []
            pending: list[str] = []
            trading_today = self.is_trading_day(today)

            if not trading_today:
                self._log(f"[SKIP] {today} 非交易日（周末/法定休市），跳过全部任务")
                return self._result(now, False, executed, failed, pending,
                                    reason="non_trading_day")

            for offset in range(self.catch_up_days + 1):
                d = today - timedelta(days=offset)
                if not self.is_trading_day(d):
                    continue
                day_key = d.isoformat()
                done_today = state.setdefault(day_key, {})
                for task in self.tasks:
                    tid = task["id"]
                    if not force and tid in done_today:
                        continue
                    # 只补「已到点」的任务：过去日期一律视为到点，今天则比较当前时刻
                    if offset == 0 and hhmm < task["time"]:
                        pending.append(tid)
                        continue
                    try:
                        last = ""
                        for step in task.get("steps", []):
                            last = self._run_step(step)
                        done_today[tid] = now.isoformat(timespec="seconds")
                        executed.append(tid)
                        self._log(f"[RUN ] {day_key} {tid} ({task.get('label','')}) OK :: {last}")
                    except Exception as exc:
                        failed.append({"task": tid, "error": f"{type(exc).__name__}: {exc}"})
                        self._log(f"[FAIL] {day_key} {tid} :: {exc}")

            self._save_state(state)
            return self._result(now, True, executed, failed, pending, reason="")

    def _result(self, now: datetime, trading: bool, executed, failed, pending,
                reason: str) -> dict:
        return {
            "now": now.isoformat(timespec="seconds"),
            "trading_day": trading,
            "executed": executed,
            "failed": failed,
            "pending": pending,
            "reason": reason,
            "version": VERSION,
        }

    # ------------------------------------------------------------------ #
    # threading.Timer 循环（进程存活期间的日内定时）
    # ------------------------------------------------------------------ #
    def _arm_timer(self) -> None:
        if self._stop.is_set():
            return
        t = threading.Timer(self.tick_interval, self._on_timer)
        t.daemon = True
        t.name = "scheduler-tick"
        self._timer = t
        t.start()

    def _on_timer(self) -> None:
        try:
            self.tick()
        except Exception as exc:
            self._log(f"[ERROR] tick 异常: {type(exc).__name__}: {exc}")
        finally:
            self._arm_timer()

    def start(self) -> None:
        self._rotate_log()
        self._log(f"[BOOT] embedded_scheduler v{VERSION} tz={self.cfg.get('timezone')} "
                 f"python={self.python_bin} tasks={len(self.tasks)} "
                 f"tick_interval={self.tick_interval}s config={self.cfg.get('_source','')}")
        self._arm_timer()
        # 启动即补跑：异步执行，避免阻塞 Flask 启动导致外部探针超时
        threading.Thread(target=self.tick, daemon=True, name="startup-tick").start()

    def stop(self) -> None:
        self._stop.set()
        if self._timer:
            self._timer.cancel()

    # ------------------------------------------------------------------ #
    def check_token(self, supplied: str) -> bool:
        if not self.token:
            return True
        return hmac.compare_digest(supplied or "", self.token)

    def health(self) -> dict:
        now = datetime.now(self.tz)
        state = self._load_state()
        done = state.get(now.date().isoformat(), {})
        pending = [t["id"] for t in self.tasks
                   if t["id"] not in done and now.strftime("%H:%M") >= t["time"]]
        return {
            "status": "ok",
            "version": VERSION,
            "now": now.isoformat(timespec="seconds"),
            "timezone": str(self.tz),
            "trading_day": self.is_trading_day(now.date()),
            "scheduler_running": not self._stop.is_set(),
            "timer_alive": bool(self._timer and self._timer.is_alive()),
            "seconds_since_last_tick": (
                int((now - self._last_tick).total_seconds()) if self._last_tick else None),
            "today_completed": done,
            "pending_now": pending,
            "auth": "token" if self.token else "open",
            "python": self.python_bin,
        }


def create_app(sched: EmbeddedScheduler, Flask, jsonify, request):
    app = Flask(__name__)

    @app.route("/", methods=["GET"])
    def index():
        return jsonify({
            "service": "quant-skills embedded scheduler",
            "version": VERSION,
            "endpoints": ["/health", "/wakeup"],
            "docs": "docs/ENVIRONMENT_NOTES.md",
        })

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(sched.health())

    @app.route("/wakeup", methods=["GET", "POST"])
    def wakeup():
        supplied = (request.args.get("token")
                    or request.headers.get("X-Wakeup-Token", ""))
        if not sched.check_token(supplied):
            return jsonify({"error": "unauthorized"}), 401
        sync = request.args.get("sync") == "1"
        force = request.args.get("force") == "1"
        if sync:
            result = sched.tick(force=force)
            return jsonify({"accepted": True, "mode": "sync", "result": result}), (
                500 if result["failed"] else 200)
        # 默认异步：外部定时器只需「敲门」，不必等任务跑完，避免 HTTP 超时误判重试
        threading.Thread(target=sched.tick, kwargs={"force": force},
                         daemon=True, name="wakeup-tick").start()
        return jsonify({"accepted": True, "mode": "async",
                        "hint": "用 /health 查看执行结果，或加 ?sync=1 同步等待"}), 202

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="内嵌调度引擎（云沙箱方案 B）")
    ap.add_argument("--config", help="调度配置路径，相对仓库根目录")
    ap.add_argument("--tick-once", action="store_true",
                    help="不启动 Flask，只执行一次 tick（纯标准库，可作为外部兜底命令）")
    ap.add_argument("--force", action="store_true", help="忽略状态记录，强制重跑当天任务")
    ap.add_argument("--host", help="覆盖监听地址")
    ap.add_argument("--port", type=int, help="覆盖监听端口")
    args = ap.parse_args()

    sched = EmbeddedScheduler(load_config(args.config))

    if args.tick_once:
        result = sched.tick(force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["failed"] else 0

    try:
        from flask import Flask, jsonify, request
    except ImportError:
        print("[ERROR] 需要 Flask：python3 -m pip install flask "
              "（或用 --tick-once 模式，纯标准库即可）", file=sys.stderr)
        return 2

    app = create_app(sched, Flask, jsonify, request)
    sched.start()
    atexit.register(sched.stop)
    host = args.host or sched.cfg.get("host", "0.0.0.0")
    port = args.port or int(sched.cfg.get("port", 8080))
    print(f"[INFO] Flask 监听 http://{host}:{port}  (/health, /wakeup)")
    app.run(host=host, port=port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
