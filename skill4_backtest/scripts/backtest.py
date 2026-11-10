#!/usr/bin/env python3
"""Skill 4 · 轻量级回测引擎（纯离线历史数据推演）。

不联网、不读实时行情、不依赖任何第三方库：仅用标准库读取一份长表 CSV，
逐交易日复用 Skill 2 的 weights.json 打分 → 取 TopN → 等权持有 → 按
forward_return_pct 结算 → 扣双边手续费 → 累计净值并输出绩效报告。

用法：
  python3 scripts/backtest.py
  python3 scripts/backtest.py --history data/my_history.csv --start 2026-11-02 --end 2026-11-13 --top 3
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from config_loader import ensure_parent, load_config, resolve

OPS = {
    ">=": lambda x, v: x >= v, ">": lambda x, v: x > v,
    "<=": lambda x, v: x <= v, "<": lambda x, v: x < v,
    "==": lambda x, v: x == v,
    "between": lambda x, v: v[0] <= x <= v[1],
}


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def band_score(value: float | None, bands: list, clip: list[float]) -> float:
    """与 Skill 2 完全相同的分段规则：min <= x < max，null 表示无穷。"""
    if value is None:
        return clip[0]
    for lo, hi, s in bands:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return min(max(float(s), clip[0]), clip[1])
    return float(bands[-1][2])


def pass_filters(row: dict, filters: list[dict]) -> bool:
    for f in filters:
        v = _num(row.get(f["key"]))
        if v is None or f["op"] not in OPS or not OPS[f["op"]](v, f["value"]):
            return False
    return True


def score(row: dict, weights: dict) -> float:
    clip = weights["normalization"].get("clip", [0.0, 1.0])
    total = sum(f["weight"] * band_score(_num(row.get(f["column"])), f["bands"], clip)
                for f in weights["factors"])
    return round(100 * total, weights["scale"]["round"])


def read_history(path: Path, date_col: str) -> dict[str, list[dict]]:
    if not path.exists():
        raise FileNotFoundError(f"历史数据不存在: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if date_col not in (rows[0] if rows else {}):
        raise ValueError(f"CSV 缺少日期列 {date_col}")
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(str(r[date_col]).strip(), []).append(r)
    return by_date


def run(cfg: dict, weights: dict, args: argparse.Namespace) -> dict:
    bt = cfg["backtest"]
    hist_path = resolve(args.history or cfg["input"]["history_csv"])
    by_date = read_history(hist_path, cfg["input"]["date_column"])
    dates = sorted(by_date)
    start, end = args.start or bt.get("start_date") or "", args.end or bt.get("end_date") or ""
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if not dates:
        raise ValueError("筛选后无交易日，请检查 start_date / end_date")

    top_n = args.top or int(bt.get("top_n", 3))
    fee_pct = float(bt.get("commission_rate", 0.0)) * 100 * 2  # 双边，换算成百分点
    capital = float(bt.get("initial_capital", 50000))
    tdp = float(bt.get("trading_days_per_year", 242))
    fwd_col = cfg["input"]["forward_return_column"]
    filters = weights.get("hard_filters", [])

    equity, peak = 1.0, 1.0
    curve, daily_rets, pick_log = [], [], []
    max_dd = 0.0

    for d in dates:
        candidates = [r for r in by_date[d] if pass_filters(r, filters)]
        ranked = sorted(candidates, key=lambda r: -score(r, weights))[:top_n]
        if not ranked:
            daily_ret = 0.0
        else:
            rets = [_num(r.get(fwd_col)) or 0.0 for r in ranked]
            daily_ret = statistics.fmean(rets) - fee_pct
        equity *= (1 + daily_ret / 100)
        peak = max(peak, equity)
        dd = (equity / peak - 1) * 100
        max_dd = min(max_dd, dd)
        daily_rets.append(daily_ret)
        curve.append({"date": d, "daily_return_pct": round(daily_ret, 4),
                      "equity": round(equity, 6), "equity_value": round(capital * equity, 2),
                      "drawdown_pct": round(dd, 4), "picks": len(ranked),
                      "codes": ",".join(str(r.get("code", "")) for r in ranked)})
        pick_log.append({"date": d, "top": [
            {"code": r.get("code"), "name": r.get("name"), "score": score(r, weights),
             "forward_return_pct": _num(r.get(fwd_col))} for r in ranked]})

    n = len(daily_rets)
    total_ret = (equity - 1) * 100
    # 样本过短时年化会指数级失真（例如 10 日样本年化数万 %），直接置空并给出提示
    min_days_for_annual = max(20, int(tdp / 4))
    ann_reliable = n >= min_days_for_annual
    ann_ret = ((equity ** (tdp / n)) - 1) * 100 if ann_reliable and n else None
    wins = sum(1 for r in daily_rets if r > 0)
    sd = statistics.pstdev(daily_rets) if n > 1 else 0.0
    sharpe = (statistics.fmean(daily_rets) / sd * math.sqrt(tdp)) if sd else 0.0
    trades = sum(c["picks"] for c in curve)
    win_trades = sum(1 for day in pick_log for p in day["top"]
                     if (p["forward_return_pct"] or 0) > 0)

    return {
        "meta": {
            "history_file": hist_path.name, "weights_version": weights["version"],
            "normalization": weights["normalization"]["method"],
            "start": dates[0], "end": dates[-1], "trading_days": n,
            "top_n": top_n, "commission_rate": bt.get("commission_rate"),
            "offline": True,
        },
        "metrics": {
            "total_return_pct": round(total_ret, 2),
            "annualized_return_pct": round(ann_ret, 2) if ann_ret is not None else None,
            "annualized_note": "" if ann_reliable else
                f"样本仅 {n} 个交易日（少于 {min_days_for_annual} 日），年化收益不具统计意义，已置空",
            "max_drawdown_pct": round(max_dd, 2),
            "daily_win_rate_pct": round(wins / n * 100, 2) if n else 0,
            "trade_win_rate_pct": round(win_trades / trades * 100, 2) if trades else 0,
            "sharpe_ratio": round(sharpe, 3),
            "trades": trades, "final_equity": round(equity, 6),
            "final_capital": round(capital * equity, 2),
        },
        "equity_curve": curve,
        "daily_picks": pick_log,
    }


def write_outputs(result: dict, cfg: dict) -> None:
    out = cfg["output"]
    csv_path = resolve(out["equity_curve_csv"])
    ensure_parent(csv_path)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(result["equity_curve"][0].keys()))
        w.writeheader()
        w.writerows(result["equity_curve"])
    print(f"[OK] 净值曲线 -> {csv_path}")

    json_path = resolve(out["report_json"])
    ensure_parent(json_path)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 绩效 JSON -> {json_path}")

    m, meta = result["metrics"], result["meta"]
    ann_txt = (f"{m['annualized_return_pct']:+.2f}%" if m["annualized_return_pct"] is not None
               else "N/A（样本不足）")
    md_path = resolve(out["report_md"])
    ensure_parent(md_path)
    lines = [
        f"# 离线回测报告 · {meta['start']} ~ {meta['end']}", "",
        f"数据源：`{meta['history_file']}`（完全离线） · 权重 v{meta['weights_version']} · "
        f"归一化 `{meta['normalization']}` · Top{meta['top_n']}", "",
        "## 绩效指标", "", "| 指标 | 数值 |", "| --- | ---: |",
        f"| 回测交易日 | {meta['trading_days']} |",
        f"| 累计收益率 | {m['total_return_pct']:+.2f}% |",
        f"| 年化收益率 | {ann_txt} |",
        f"| 最大回撤 | {m['max_drawdown_pct']:.2f}% |",
        f"| 日胜率 | {m['daily_win_rate_pct']:.2f}% |",
        f"| 交易胜率 | {m['trade_win_rate_pct']:.2f}% |",
        f"| 夏普比率 | {m['sharpe_ratio']:.3f} |",
        f"| 交易笔数 | {m['trades']} |",
        f"| 期末资金 | {m['final_capital']:,.2f} |", "",
        "## 净值曲线", "", "| 日期 | 日收益 | 净值 | 资金 | 回撤 | 入选 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in result["equity_curve"]:
        lines.append(f"| {c['date']} | {c['daily_return_pct']:+.2f}% | {c['equity']:.4f} | "
                     f"{c['equity_value']:,.0f} | {c['drawdown_pct']:.2f}% | {c['picks']} |")
    if m["annualized_note"]:
        lines += ["", f"> 注：{m['annualized_note']}"]
    lines += ["", "---", "", "由 Skill 4 纯离线推演生成 · 仅供策略研究，不构成投资建议。", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 可读报告 -> {md_path}")


def main() -> int:
    p = argparse.ArgumentParser(description="Skill 4 轻量级离线回测引擎")
    p.add_argument("--config", help="配置路径，默认 config.json")
    p.add_argument("--env", help=".env 路径")
    p.add_argument("--history", help="覆盖历史 CSV 路径")
    p.add_argument("--weights", help="覆盖 weights.json 路径")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD")
    p.add_argument("--top", type=int, help="覆盖 top_n")
    args = p.parse_args()

    cfg = load_config(args.config, args.env)
    weights_path = resolve(args.weights or cfg["weights_file"])
    weights = json.loads(weights_path.read_text(encoding="utf-8"))
    result = run(cfg, weights, args)
    m = result["metrics"]
    print("\n=== 离线回测绩效 ===")
    print(f"区间 {result['meta']['start']} ~ {result['meta']['end']}  "
          f"共 {result['meta']['trading_days']} 个交易日")
    ann_show = (f"{m['annualized_return_pct']:+.2f}%" if m["annualized_return_pct"] is not None
                else "N/A(样本不足)")
    print(f"累计收益 {m['total_return_pct']:+.2f}%  年化 {ann_show}  "
          f"最大回撤 {m['max_drawdown_pct']:.2f}%  夏普 {m['sharpe_ratio']:.3f}")
    print(f"日胜率 {m['daily_win_rate_pct']:.2f}%  交易胜率 {m['trade_win_rate_pct']:.2f}%  "
          f"交易 {m['trades']} 笔  期末 {m['final_capital']:,.2f}")
    write_outputs(result, cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
