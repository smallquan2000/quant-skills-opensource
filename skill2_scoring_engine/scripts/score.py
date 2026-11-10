#!/usr/bin/env python3
"""Skill 2 · 打分引擎 —— 五维权重 100% 由 weights.json 驱动，代码零硬编码阈值。

示例：
  python3 scripts/score.py
  python3 scripts/score.py --input ../skill1_data_pipeline/data/cb_snapshot.csv --top 3
  python3 scripts/score.py --weights weights.json --output data/scored_signals.csv

输出 scored_signals.csv / scored_signals.json，供 Skill 3（模拟盘）与 Skill 4（回测）消费。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from config_loader import ensure_parent, load_config, resolve

OPS = {
    ">=": lambda x, v: x >= v,
    ">": lambda x, v: x > v,
    "<=": lambda x, v: x <= v,
    "<": lambda x, v: x < v,
    "==": lambda x, v: x == v,
    "between": lambda x, v: v[0] <= x <= v[1],
}


# --------------------------------------------------------------------------- #
# 权重与分段
# --------------------------------------------------------------------------- #
def load_weights(path: Path) -> dict:
    w = json.loads(path.read_text(encoding="utf-8"))
    total = sum(f["weight"] for f in w["factors"])
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"权重之和必须为 1.0，当前为 {total:.4f}，请检查 weights.json")
    return w


def band_score(value: float | None, bands: list, clip: list[float]) -> float:
    """按 bands 分段映射：区间 min <= x < max，null 表示无穷。"""
    if value is None:
        return clip[0]
    for lo, hi, score in bands:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return min(max(float(score), clip[0]), clip[1])
    # 未命中任何区间（理论上不该发生），取最后一段
    return float(bands[-1][2])


def minmax_scores(rows: list[dict], factors: list[dict]) -> dict[str, dict[str, float]]:
    """可选：当日横截面归一化。返回 {code: {factor_key: score}}。"""
    result: dict[str, dict[str, float]] = {str(r["code"]): {} for r in rows}
    for f in factors:
        col, key = f["column"], f["key"]
        vals = [_num(r.get(col)) for r in rows]
        valid = [v for v in vals if v is not None]
        lo, hi = (min(valid), max(valid)) if valid else (0.0, 1.0)
        span = hi - lo or 1.0
        for r in rows:
            v = _num(r.get(col))
            s = 0.0 if v is None else (v - lo) / span
            if f["direction"] == "lower_better":
                s = 1.0 - s
            result[str(r["code"])][key] = min(max(s, 0.0), 1.0)
    return result


def pass_filters(row: dict, filters: list[dict]) -> tuple[bool, str]:
    for flt in filters:
        val = _num(row.get(flt["key"]))
        if val is None:
            return False, f"{flt['key']} 缺失"
        op = OPS.get(flt["op"])
        if op is None:
            raise ValueError(f"不支持的过滤操作: {flt['op']}")
        if not op(val, flt["value"]):
            return False, flt.get("label", flt["key"])
    return True, ""


def _num(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 输入
# --------------------------------------------------------------------------- #
def read_rows(cfg: dict, input_arg: str | None, weights: dict) -> list[dict]:
    icfg = cfg["input"]
    if input_arg and input_arg.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return read_sqlite(resolve(input_arg), icfg, weights)
    if input_arg:
        path = resolve(input_arg)
    else:
        path = resolve(icfg.get("csv_path", ""))
        if not path.exists() and icfg.get("fallback_csv"):
            path = resolve(icfg["fallback_csv"])
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在: {path}；请先运行 Skill 1 数据管道")
    if not str(path).lower().endswith(".csv"):
        return read_sqlite(path, icfg, weights)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    date_filter = icfg.get("date") or ""
    if date_filter:
        rows = [r for r in rows if str(r.get("date", "")).strip() == date_filter.strip()]
    elif rows:
        latest = max(str(r.get("date", "")) for r in rows)
        rows = [r for r in rows if str(r.get("date", "")).strip() == latest]
    print(f"[INFO] 输入 {path.name}，日期 {rows[0].get('date') if rows else 'N/A'}，{len(rows)} 行")
    return rows


def read_sqlite(db_path: Path, icfg: dict, weights: dict) -> list[dict]:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 不存在: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        table = icfg.get("table", "cb_snapshot")
        d = icfg.get("date") or ""
        if d:
            cur = conn.execute(f"SELECT * FROM {table} WHERE date = ?", (d,))
        else:
            cur = conn.execute(f"SELECT * FROM {table} WHERE date = (SELECT MAX(date) FROM {table})")
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    print(f"[INFO] 输入 SQLite {db_path.name}，{len(rows)} 行")
    return rows


# --------------------------------------------------------------------------- #
# 打分主流程
# --------------------------------------------------------------------------- #
def score_rows(rows: list[dict], weights: dict) -> tuple[list[dict], list[dict]]:
    method = weights["normalization"]["method"]
    clip = weights["normalization"].get("clip", [0.0, 1.0])
    factors = weights["factors"]
    filters = weights.get("hard_filters", [])
    cross = minmax_scores(rows, factors) if method == "minmax" else {}

    passed, rejected = [], []
    for row in rows:
        ok, reason = pass_filters(row, filters)
        if not ok:
            rejected.append({"code": row.get("code"), "name": row.get("name"), "reason": reason})
            continue
        detail: dict[str, float] = {}
        total = 0.0
        for f in factors:
            if method == "minmax":
                s = cross.get(str(row["code"]), {}).get(f["key"], 0.0)
            else:
                s = band_score(_num(row.get(f["column"])), f["bands"], clip)
            detail[f["key"]] = round(s, 4)
            total += f["weight"] * s
        item = dict(row)
        item["score"] = round(100 * total, weights["scale"]["round"])
        for k, v in detail.items():
            item[f"f_{k}"] = v
        passed.append(item)

    passed.sort(key=lambda r: (-r["score"], str(r["code"])))
    for i, r in enumerate(passed, 1):
        r["rank"] = i
    return passed, rejected


def write_outputs(ranked: list[dict], top: list[dict], rejected: list[dict],
                  cfg: dict, weights: dict, weights_path: Path) -> None:
    out = cfg["output"]
    factor_keys = [f["key"] for f in weights["factors"]]
    columns = (["rank", "code", "name", "date", "score"]
               + [f"f_{k}" for k in factor_keys]
               + ["price", "cb_change_pct", "stock_change_pct", "cb_stock_gap",
                  "volume_ratio", "premium_rate", "amount"])

    csv_path = resolve(out["csv"])
    ensure_parent(csv_path)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in top:
            w.writerow(r)
    print(f"[OK] Top{len(top)} 信号 -> {csv_path}")

    json_path = resolve(out["json"])
    ensure_parent(json_path)
    payload = {
        "meta": {
            "weights_version": weights["version"],
            "weights_file": weights_path.name,
            "normalization": weights["normalization"]["method"],
            "top_n": len(top),
            "universe_count": len(ranked),
            "rejected_count": len(rejected),
        },
        "signals": [{k: r.get(k) for k in columns} for r in top],
        "rejected": rejected,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 完整结果 -> {json_path}")


def main() -> int:
    p = argparse.ArgumentParser(description="Skill 2 五维打分引擎（权重全部来自 weights.json）")
    p.add_argument("--config", help="配置路径，默认 config.json")
    p.add_argument("--env", help=".env 路径")
    p.add_argument("--input", help="覆盖输入 CSV / SQLite 路径")
    p.add_argument("--weights", help="覆盖 weights.json 路径")
    p.add_argument("--output", help="覆盖输出 CSV 路径")
    p.add_argument("--top", type=int, help="覆盖 top_n")
    args = p.parse_args()

    cfg = load_config(args.config, args.env)
    weights_path = resolve(args.weights or cfg["weights_file"])
    weights = load_weights(weights_path)
    rows = read_rows(cfg, args.input, weights)
    if not rows:
        print("[WARN] 无输入数据", file=sys.stderr)
        return 1

    ranked, rejected = score_rows(rows, weights)
    top_n = args.top or cfg.get("top_n_override") or weights["top_n"]
    top = ranked[:top_n]
    if args.output:
        cfg["output"]["csv"] = args.output

    print(f"\n=== 打分结果（{weights['normalization']['method']}，权重 v{weights['version']}）===")
    print(f"{'#':<3}{'代码':<10}{'名称':<12}{'价格':>9}{'正股':>8}{'缺口':>8}{'量比':>7}{'溢价':>8}{'得分':>9}")
    for r in ranked:
        mark = "★" if r in top else " "
        print(f"{mark}{r['rank']:<2}{str(r['code']):<10}{str(r.get('name','')):<12}"
              f"{_fmt(r.get('price')):>9}{_fmt(r.get('stock_change_pct')):>8}"
              f"{_fmt(r.get('cb_stock_gap')):>8}{_fmt(r.get('volume_ratio')):>7}"
              f"{_fmt(r.get('premium_rate')):>8}{r['score']:>9.2f}")
    print(f"\n通过硬过滤 {len(ranked)} 只，淘汰 {len(rejected)} 只，推送 Top{len(top)}")
    for r in rejected[:5]:
        print(f"  - {r['code']} {r['name']}: {r['reason']}")

    write_outputs(ranked, top, rejected, cfg, weights, weights_path)
    return 0


def _fmt(v: Any) -> str:
    n = _num(v)
    return "-" if n is None else f"{n:.2f}"


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
