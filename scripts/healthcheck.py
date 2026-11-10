#!/usr/bin/env python3
"""系统自检（零第三方依赖）。

检查：Python 版本与依赖、各 Skill 配置、关键数据文件新鲜度、可选进程存活。
输出 JSON 报告，存在 FAIL 时返回退出码 1（便于 crontab / watchdog 捕获告警）。

用法：
  python3 scripts/healthcheck.py
  python3 scripts/healthcheck.py --max-age-hours 24 --check-process cron
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILLS = ["skill1_data_pipeline", "skill2_scoring_engine",
          "skill3_paper_trading", "skill4_backtest"]

# 关键产物：路径相对仓库根目录；fresh=是否需要检查新鲜度
WATCH_FILES = [
    ("skill1_data_pipeline/data/cb_market.db", True),
    ("skill1_data_pipeline/data/cb_snapshot.csv", True),
    ("skill2_scoring_engine/data/scored_signals.csv", True),
    ("skill3_paper_trading/data/paper_account.json", False),
    ("skill4_backtest/data/backtest_report.json", False),
]


def check(label: str, ok: bool, detail: str, level: str = "FAIL") -> dict:
    return {"check": label, "status": "OK" if ok else level, "detail": detail}


def main() -> int:
    p = argparse.ArgumentParser(description="量化 Skill 系统自检")
    p.add_argument("--max-age-hours", type=float, default=48.0,
                   help="关键数据文件允许的最大陈旧时长（小时）")
    p.add_argument("--check-process", default="",
                   help="可选：检查指定进程名是否存活（如 cron）")
    p.add_argument("--json", action="store_true", help="只输出 JSON")
    args = p.parse_args()

    results = []
    py = sys.version_info
    results.append(check("python_version", py >= (3, 9),
                         f"{py.major}.{py.minor}.{py.micro}（要求 >=3.9）"))

    for mod in ("csv", "json", "sqlite3", "smtplib"):
        results.append(check(f"stdlib_{mod}", importlib.util.find_spec(mod) is not None,
                             f"标准库 {mod} 可用"))
    # requests 仅 Skill 1 eastmoney / webhook 需要，缺失只给 WARN
    has_requests = importlib.util.find_spec("requests") is not None
    results.append(check("optional_requests", has_requests,
                         "requests 已安装" if has_requests else
                         "requests 未安装（仅在线数据源 / webhook 需要）", level="WARN"))

    for s in SKILLS:
        cfg = ROOT / s / "config.json"
        example = ROOT / s / "config.example.json"
        if cfg.exists():
            results.append(check(f"config_{s}", True, "使用 config.json"))
        elif example.exists():
            results.append(check(f"config_{s}", True,
                                 "演示模式：自动回落 config.example.json（无需配置）"))
        else:
            results.append(check(f"config_{s}", False, "缺少配置文件"))

    cutoff = datetime.now() - timedelta(hours=args.max_age_hours)
    for rel, need_fresh in WATCH_FILES:
        f = ROOT / rel
        if not f.exists():
            results.append(check(f"file_{rel}", False, "尚未生成（首次运行前属正常）", level="WARN"))
            continue
        age_h = (datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 3600
        ok = (age_h <= args.max_age_hours) if need_fresh else True
        results.append(check(f"freshness_{rel}", ok,
                             f"最后更新 {age_h:.1f} 小时前" + ("" if ok else "（已陈旧）"),
                             level="WARN"))

    if args.check_process:
        try:
            r = subprocess.run(["pgrep", "-f", args.check_process],
                               capture_output=True, timeout=5)
            alive = r.returncode == 0
        except Exception as exc:
            alive, detail = False, str(exc)
        else:
            detail = f"pgrep -f {args.check_process}"
        results.append(check(f"process_{args.check_process}", alive, detail, level="WARN"))

    fails = [r for r in results if r["status"] == "FAIL"]
    warns = [r for r in results if r["status"] == "WARN"]
    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {"ok": len(results) - len(fails) - len(warns),
                    "warn": len(warns), "fail": len(fails),
                    "healthy": len(fails) == 0},
        "results": results,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== 系统自检 {report['checked_at']} ===")
        for r in results:
            print(f"  [{r['status']:<4}] {r['check']:<45} {r['detail']}")
        print(f"\n结论：{'✅ 健康' if not fails else '❌ 存在故障'}  "
              f"(OK {report['summary']['ok']} / WARN {len(warns)} / FAIL {len(fails)})")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
