#!/usr/bin/env python3
"""Skill 3 · 模拟盘执行与复盘。

仓位 / 止盈止损 / 邮件通知全部由 config.json 驱动，代码不含任何业务常数。

用法：
  python3 scripts/paper_engine.py --mode status
  python3 scripts/paper_engine.py --mode buy
  python3 scripts/paper_engine.py --mode review
  python3 scripts/paper_engine.py --mode reset

流程：buy（读 Skill 2 的 Top3 信号建仓）→ review（按当前价判定止盈/止损/T+0 尾盘平仓）
      → 生成 Markdown 复盘报告 + trade_log.csv + 即时通知。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import ensure_parent, load_config, resolve
from notifier import Notifier

TRADE_COLUMNS = [
    "ts", "date", "code", "name", "action", "price", "shares",
    "amount", "commission", "pnl", "pnl_pct", "reason",
]


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pick_input(cfg: dict, key: str, fallback_key: str) -> Path | None:
    primary = resolve(cfg["input"][key])
    if primary.exists():
        return primary
    fb = cfg["input"].get(fallback_key)
    if fb:
        path = resolve(fb)
        if path.exists():
            print(f"[INFO] 主输入不存在，回落 {path.name}")
            return path
    return None


class PaperAccount:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.acct = cfg["account"]
        self.rules = cfg["exit_rules"]
        self.state_path = resolve(cfg["paths"]["account_state"])
        self.log_path = resolve(cfg["paths"]["trade_log"])
        self.review_dir = resolve(cfg["paths"]["review_dir"])
        self.notifier = Notifier(cfg)
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "initial_capital": self.acct["initial_capital"],
            "cash": self.acct["initial_capital"],
            "positions": {},
            "realized_pnl": 0.0,
            "equity_curve": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def save(self) -> None:
        self.state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        ensure_parent(self.state_path)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, row: dict) -> None:
        ensure_parent(self.log_path)
        exists = self.log_path.exists()
        with self.log_path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerow(row)

    def _notify_instant(self, subject: str, body: str) -> str:
        if self.cfg["notify"].get("instant_trigger", True):
            return self.notifier.send(subject, body)
        return "deferred"

    # ------------------------------------------------------------------ #
    def equity(self, price_map: dict[str, float]) -> float:
        pos_value = sum(
            p["shares"] * price_map.get(code, p["entry_price"])
            for code, p in self.state["positions"].items()
        )
        return self.state["cash"] + pos_value

    def status(self, price_map: dict[str, float] | None = None) -> None:
        pm = price_map or {}
        eq = self.equity(pm)
        init = self.state["initial_capital"]
        print(f"\n=== 模拟盘账户 ===")
        print(f"初始资金 : {init:,.2f} {self.acct.get('currency', 'CNY')}")
        print(f"可用现金 : {self.state['cash']:,.2f}")
        print(f"总资产   : {eq:,.2f}  ({(eq / init - 1) * 100:+.2f}%)")
        print(f"已实现盈亏: {self.state['realized_pnl']:+,.2f}")
        print(f"持仓数   : {len(self.state['positions'])}")
        for code, p in self.state["positions"].items():
            cur = pm.get(code, p["entry_price"])
            print(f"  {code} {p['name']:<10} {p['shares']:>4}张 成本 {p['entry_price']:.2f} "
                  f"现价 {cur:.2f} 浮动 {(cur / p['entry_price'] - 1) * 100:+.2f}%")

    # ------------------------------------------------------------------ #
    def buy(self, signals: list[dict]) -> None:
        today = signals[0].get("date", datetime.now().strftime("%Y-%m-%d")) if signals else ""
        max_pos = int(self.acct["max_positions"])
        budget_each = self.state["cash"] * float(self.acct["position_pct"])
        lot = int(self.acct.get("lot_size", 10))
        rate = float(self.acct["commission_rate"])
        opened = 0

        for s in signals:
            if len(self.state["positions"]) >= max_pos:
                break
            code = str(s["code"]).strip()
            if code in self.state["positions"]:
                continue
            price = _f(s.get("price"))
            if price <= 0:
                continue
            lots = int(budget_each // (price * lot))
            shares = lots * lot
            if shares <= 0:
                print(f"[SKIP] {code} 资金不足以买入 1 手")
                continue
            amount = shares * price
            fee = round(amount * rate, 2)
            if amount + fee > self.state["cash"]:
                continue
            self.state["cash"] -= amount + fee
            self.state["positions"][code] = {
                "name": s.get("name", ""), "shares": shares,
                "entry_price": price, "entry_date": today,
                "entry_score": _f(s.get("score")), "entry_fee": fee,
                "peak_price": price,
            }
            body = (f"【建仓】{code} {s.get('name','')}\n"
                    f"价格 {price:.2f} × {shares}张 = {amount:,.2f}，手续费 {fee:.2f}\n"
                    f"打分 {s.get('score','-')} 分，日期 {today}")
            st = self._notify_instant("建仓信号", body)
            self._log({"ts": datetime.now().isoformat(timespec="seconds"), "date": today,
                       "code": code, "name": s.get("name", ""), "action": "BUY",
                       "price": price, "shares": shares, "amount": round(amount, 2),
                       "commission": fee, "pnl": 0, "pnl_pct": 0, "reason": f"Top{opened+1} 信号 / {st}"})
            opened += 1
        self.save()
        print(f"[OK] 本次建仓 {opened} 只，剩余现金 {self.state['cash']:,.2f}")

    # ------------------------------------------------------------------ #
    def review(self, price_map: dict[str, float]) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        tp = float(self.rules["take_profit_pct"])
        sl = float(self.rules["stop_loss_pct"])
        trail = float(self.rules.get("trailing_stop_pct", 0))
        close_eod = bool(self.rules.get("t0_close_at_eod", True))
        rate = float(self.acct["commission_rate"])
        closed_rows = []

        for code, p in list(self.state["positions"].items()):
            cur = price_map.get(code, p["entry_price"])
            peak = max(p.get("peak_price", p["entry_price"]), cur)
            p["peak_price"] = peak
            pnl_pct = (cur / p["entry_price"] - 1) * 100
            drawdown_from_peak = (cur / peak - 1) * 100 if peak else 0.0

            if pnl_pct >= tp:
                reason = f"止盈 +{tp:.2f}%"
            elif pnl_pct <= sl:
                reason = f"止损 {sl:.2f}%"
            elif trail and cur > p["entry_price"] and drawdown_from_peak <= -trail:
                # 只有已有浮盈、且从峰值回撤时才叫「移动止盈」；浮亏不应误判
                reason = f"移动止盈 回撤 {drawdown_from_peak:.2f}%"
            elif close_eod:
                reason = "T+0 尾盘平仓"
            else:
                self.state["positions"][code] = p
                continue

            amount = cur * p["shares"]
            fee = round(amount * rate, 2)
            pnl = amount - p["entry_price"] * p["shares"] - fee - p.get("entry_fee", 0)
            pnl_pct_net = pnl / (p["entry_price"] * p["shares"]) * 100
            self.state["cash"] += amount - fee
            self.state["realized_pnl"] += pnl
            del self.state["positions"][code]

            row = {"ts": datetime.now().isoformat(timespec="seconds"), "date": today,
                   "code": code, "name": p["name"], "action": "SELL", "price": cur,
                   "shares": p["shares"], "amount": round(amount, 2), "commission": fee,
                   "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct_net, 2), "reason": reason}
            self._log(row)
            closed_rows.append(row)
            st = self._notify_instant("平仓信号",
                f"【平仓】{code} {p['name']}  {cur:.2f} × {p['shares']}张\n"
                f"盈亏 {pnl:+,.2f} ({pnl_pct_net:+.2f}%)  原因：{reason}")
            row["notify"] = st

        eq = self.equity(price_map)
        self.state["equity_curve"].append({"date": today, "equity": round(eq, 2)})
        self.save()
        self._write_review(closed_rows, eq, price_map)
        print(f"[OK] 平仓 {len(closed_rows)} 笔，总资产 {eq:,.2f}")

    def _write_review(self, rows: list[dict], equity: float, price_map: dict[str, float]) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        ensure_parent(self.review_dir / "placeholder")
        path = self.review_dir / f"review_{today}.md"
        init = self.state["initial_capital"]
        wins = [r for r in rows if r["pnl"] > 0]
        lines = [
            f"# 模拟盘复盘 · {today}", "",
            "## 账户总览", "",
            "| 项目 | 数值 |", "| --- | --- |",
            f"| 初始资金 | {init:,.2f} |",
            f"| 总资产 | {equity:,.2f} |",
            f"| 累计收益率 | {(equity / init - 1) * 100:+.2f}% |",
            f"| 已实现盈亏 | {self.state['realized_pnl']:+,.2f} |",
            f"| 可用现金 | {self.state['cash']:,.2f} |",
            f"| 剩余持仓 | {len(self.state['positions'])} |", "",
            "## 当日平仓", "",
        ]
        if rows:
            lines += ["| 代码 | 名称 | 价格 | 张数 | 盈亏 | 收益率 | 原因 |",
                      "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
            for r in rows:
                lines.append(f"| {r['code']} | {r['name']} | {r['price']:.2f} | {r['shares']} | "
                             f"{r['pnl']:+,.2f} | {r['pnl_pct']:+.2f}% | {r['reason']} |")
            lines += ["", f"**胜率**：{len(wins)}/{len(rows)} = {len(wins)/len(rows)*100:.1f}%"]
        else:
            lines.append("当日无平仓。")
        lines += ["", "## 剩余持仓", ""]
        if self.state["positions"]:
            for code, p in self.state["positions"].items():
                cur = price_map.get(code, p["entry_price"])
                lines.append(f"- {code} {p['name']}：{p['shares']}张，成本 {p['entry_price']:.2f}，"
                             f"现价 {cur:.2f}，浮动 {(cur/p['entry_price']-1)*100:+.2f}%")
        else:
            lines.append("空仓。")
        lines += ["", "---", "", "由 Skill 3 自动生成 · 仅供策略研究，不构成投资建议。", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[OK] 复盘报告 -> {path}")
        self._notify_instant("每日复盘", f"总资产 {equity:,.2f}（{(equity/init-1)*100:+.2f}%），"
                            f"当日平仓 {len(rows)} 笔，胜率 {len(wins)}/{len(rows) if rows else 1}")


def main() -> int:
    p = argparse.ArgumentParser(description="Skill 3 模拟盘执行与复盘")
    p.add_argument("--config", help="配置路径，默认 config.json")
    p.add_argument("--env", help=".env 路径")
    p.add_argument("--mode", choices=["status", "buy", "review", "reset"], default="status")
    p.add_argument("--prices", help="覆盖当前价，格式 code=price,code=price")
    args = p.parse_args()

    cfg = load_config(args.config, args.env)
    acct = PaperAccount(cfg)

    if args.mode == "reset":
        ensure_parent(acct.state_path)
        acct.state_path.write_text(json.dumps({
            "initial_capital": cfg["account"]["initial_capital"],
            "cash": cfg["account"]["initial_capital"],
            "positions": {}, "realized_pnl": 0.0, "equity_curve": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[OK] 账户已重置；历史流水保留在 trade_log.csv")
        return 0

    price_map: dict[str, float] = {}
    rp = cfg["input"].get("review_prices_csv")
    if rp and resolve(rp).exists():
        price_map = {str(r["code"]).strip(): _f(r["current_price"])
                     for r in load_csv(resolve(rp))}
    if args.prices:
        for pair in args.prices.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                price_map[k.strip()] = _f(v)

    if args.mode == "status":
        acct.status(price_map)
    elif args.mode == "buy":
        src = pick_input(cfg, "signals_csv", "fallback_signals_csv")
        if src is None:
            print("[ERROR] 找不到信号 CSV，请先运行 Skill 2 打分引擎", file=sys.stderr)
            return 1
        acct.buy(load_csv(src))
        acct.status(price_map)
    elif args.mode == "review":
        acct.review(price_map)
        acct.status(price_map)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
