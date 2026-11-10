#!/usr/bin/env python3
"""Skill 1 · 量化数据管道 —— 采集可转债快照，归一化为标准 schema 并落 SQLite。

参数化三要素：标的池(universe)、日期(date)、数据库路径(db)。
所有路径相对本 Skill 根目录解析，绝不硬编码绝对路径。

示例：
  python3 scripts/fetch_cb_data.py --date 2026-11-10 --db data/cb_market.db
  python3 scripts/fetch_cb_data.py --provider csv --csv data/cb_sample.csv --export-csv data/cb_snapshot.csv
  python3 scripts/fetch_cb_data.py --provider eastmoney --universe 113050,123138
  python3 scripts/fetch_cb_data.py --provider csv --dry-run

数据源 provider：
  csv        —— 离线 / 跨 AI 通用（标准 CSV 契约，推荐给豆包、Claude 等使用）
  eastmoney  —— 东方财富 push2delay A 股可转债板块（原项目 b:MK0354）
  westock    —— WorkBuddy 环境由 AI 侧调用 westock CLI 取数后按 CSV 契约落盘；
                本脚本不解析 CLI 文本输出，统一用 csv provider 入库（见 SKILL.md）。
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config_loader import SKILL_ROOT, ensure_parent, load_config, resolve
from trading_calendar import (
    holidays_from_config,
    is_trading_day,
    parse_date,
    prev_trading_day,
)

# 全项目统一的数据契约：下游 Skill 2/3/4 都按这 10 列读取
STANDARD_COLUMNS = [
    "date", "code", "name", "price", "cb_change_pct", "stock_change_pct",
    "cb_stock_gap", "volume_ratio", "premium_rate", "amount",
]
NUMERIC_COLUMNS = [
    "price", "cb_change_pct", "stock_change_pct", "cb_stock_gap",
    "volume_ratio", "premium_rate", "amount",
]


# --------------------------------------------------------------------------- #
# Provider 实现
# --------------------------------------------------------------------------- #
def fetch_eastmoney(cfg: dict, trade_date: str) -> list[dict]:
    """东方财富 push2delay 接口。字段缺失时留空，由 normalize() 补齐。"""
    import requests  # 仅此 provider 需要，见 requirements.txt

    ds = cfg["data_source"]
    em = ds["eastmoney"]
    params = {
        "pn": 1,
        "pz": em.get("page_size", 500),
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": em.get("board", "b:MK0354"),
        "fields": em.get("fields", "f2,f3,f6,f10,f12,f14,f18"),
    }
    headers = {"User-Agent": ds.get("user_agent", "quant-skills/1.0")}
    resp = requests.get(em["base_url"], params=params, timeout=ds.get("timeout", 10),
                        headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    rows = (payload.get("data") or {}).get("diff") or []
    if isinstance(rows, dict):  # 部分版本返回 dict
        rows = list(rows.values())

    out = []
    for r in rows:
        price = _num(r.get("f2"))
        prev_close = _num(r.get("f18"))
        cb_change = _num(r.get("f3"))
        if cb_change is None and price and prev_close:
            cb_change = round((price / prev_close - 1) * 100, 4)
        out.append({
            "date": trade_date,
            "code": str(r.get("f12", "")).strip(),
            "name": str(r.get("f14", "")).strip(),
            "price": price,
            "cb_change_pct": cb_change,
            "stock_change_pct": _num(r.get("stock_change_pct")),  # 需外部映射补充
            "cb_stock_gap": None,
            "volume_ratio": _num(r.get("f10")),
            "premium_rate": _num(r.get("premium_rate")),          # 需外部映射补充
            "amount": _num(r.get("f6")),
        })
    return out


def fetch_csv(cfg: dict, csv_path: Path, trade_date: str | None) -> list[dict]:
    """读取符合 STANDARD_COLUMNS 契约的 CSV。"""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in STANDARD_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV 缺少标准列: {missing}；请参考 data/cb_sample.csv")
        rows = []
        for raw in reader:
            row = {c: raw.get(c, "") for c in STANDARD_COLUMNS}
            if trade_date:
                row["date"] = trade_date
            rows.append(row)
    return rows


def _num(value: Any) -> float | None:
    """把 '-' / '' / None / 非数字统一转成 None，避免脏数据污染打分。"""
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 归一化 / 过滤 / 落库
# --------------------------------------------------------------------------- #
def normalize(rows: list[dict], cfg: dict, trade_date: str) -> list[dict]:
    fb = cfg.get("fallbacks", {})
    out = []
    for r in rows:
        item: dict[str, Any] = {"date": trade_date, "code": str(r.get("code", "")).strip(),
                               "name": str(r.get("name", "")).strip()}
        if not item["code"]:
            continue
        for col in NUMERIC_COLUMNS:
            v = _num(r.get(col))
            if v is None and col in fb:
                v = _num(fb[col])
            item[col] = v
        # 债股涨幅缺口 = 正股涨幅 - 转债涨幅；缺列时按配置回填
        if item["cb_stock_gap"] is None:
            sc, cc = item["stock_change_pct"], item["cb_change_pct"]
            item["cb_stock_gap"] = round(sc - cc, 4) if sc is not None and cc is not None else _num(fb.get("cb_stock_gap"))
        out.append(item)
    return out


def filter_universe(rows: list[dict], cfg: dict, universe_arg: str | None) -> list[dict]:
    uni = cfg.get("universe", {})
    codes = [c.strip() for c in (universe_arg or "").split(",") if c.strip()]
    if not codes:
        codes = [str(c).strip() for c in uni.get("codes", []) if str(c).strip()]
    mode = uni.get("mode", "all")
    if codes and mode != "exclude":
        keep = set(codes)
        return [r for r in rows if r["code"] in keep]
    if codes and mode == "exclude":
        drop = set(codes)
        return [r for r in rows if r["code"] not in drop]
    return rows


DDL = """
CREATE TABLE IF NOT EXISTS cb_snapshot (
    date              TEXT NOT NULL,
    code              TEXT NOT NULL,
    name              TEXT,
    price             REAL,
    cb_change_pct     REAL,
    stock_change_pct  REAL,
    cb_stock_gap      REAL,
    volume_ratio      REAL,
    premium_rate      REAL,
    amount            REAL,
    source            TEXT,
    ingested_at       TEXT,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_cb_snapshot_date ON cb_snapshot(date);
"""


def save_sqlite(rows: list[dict], db_path: Path, source: str) -> int:
    ensure_parent(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(DDL)
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            """INSERT OR REPLACE INTO cb_snapshot
               (date,code,name,price,cb_change_pct,stock_change_pct,cb_stock_gap,
                volume_ratio,premium_rate,amount,source,ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(r["date"], r["code"], r["name"], r["price"], r["cb_change_pct"],
              r["stock_change_pct"], r["cb_stock_gap"], r["volume_ratio"],
              r["premium_rate"], r["amount"], source, now) for r in rows],
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def export_csv(rows: list[dict], path: Path) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STANDARD_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in STANDARD_COLUMNS})


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Skill 1 量化数据管道（参数化标的池/日期/DB）")
    p.add_argument("--config", help="配置文件路径（相对本 Skill 根目录），默认 config.json")
    p.add_argument("--env", help=".env 路径，默认 .env")
    p.add_argument("--date", help="交易日 YYYY-MM-DD，缺省取配置或今天")
    p.add_argument("--db", help="SQLite 输出路径，覆盖 paths.db")
    p.add_argument("--provider", choices=["csv", "eastmoney"], help="覆盖 data_source.provider")
    p.add_argument("--csv", help="csv provider 的输入文件，覆盖 data_source.csv_path")
    p.add_argument("--universe", help="标的池代码，逗号分隔；覆盖 universe.codes")
    p.add_argument("--export-csv", help="额外导出标准 CSV 供下游 Skill 使用")
    p.add_argument("--force", action="store_true", help="非交易日也强制执行（调试用）")
    p.add_argument("--dry-run", action="store_true", help="只打印不落库")
    args = p.parse_args()

    cfg = load_config(args.config, args.env)
    ds = cfg["data_source"]
    provider = args.provider or ds.get("provider", "csv")
    holidays = holidays_from_config(cfg)
    cal = cfg.get("calendar", {})

    trade_date = args.date or cfg.get("date") or date.today().isoformat()
    trade_date = parse_date(trade_date).isoformat()

    # 节假日 / 周末跳过（原项目踩坑：跨节假日必须找最近交易日）
    if not args.force and cal.get("skip_non_trading_day", True) and not is_trading_day(trade_date, holidays):
        prev = prev_trading_day(trade_date, holidays, cal.get("max_lookback", 15))
        print(f"[SKIP] {trade_date} 非交易日（周末/法定休市），最近交易日为 T-1 = {prev}")
        print("       本管道不执行；如需强制执行请加 --force")
        return 0

    if provider == "eastmoney":
        rows = fetch_eastmoney(cfg, trade_date)
        source = "eastmoney:push2delay"
    else:
        csv_rel = args.csv or ds.get("csv_path", "data/cb_sample.csv")
        rows = fetch_csv(cfg, resolve(csv_rel), trade_date)
        source = f"csv:{csv_rel}"

    rows = normalize(rows, cfg, trade_date)
    rows = filter_universe(rows, cfg, args.universe)
    print(f"[INFO] provider={provider} date={trade_date} 有效记录={len(rows)} 标的池模式={cfg.get('universe', {}).get('mode', 'all')}")

    if args.dry_run:
        for r in rows[:5]:
            print("  ", {k: r[k] for k in ("code", "name", "price", "cb_stock_gap", "volume_ratio")})
        return 0

    db_rel = args.db or cfg["paths"]["db"]
    db_path = resolve(db_rel)
    n = save_sqlite(rows, db_path, source)
    print(f"[OK] 已写入 SQLite {n} 行 -> {db_path}")

    export_rel = args.export_csv or cfg["paths"].get("export_csv")
    if export_rel:
        out = resolve(export_rel)
        export_csv(rows, out)
        print(f"[OK] 已导出标准 CSV -> {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 定时任务里失败要显式报错，便于自检捕获
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
