#!/usr/bin/env python3
"""生成 Skill 4 演示用离线历史数据（固定随机种子，结果可复现）。

注意：本文件产出的 sample_history.csv 是**合成数据**，仅用于演示回测流程，
不是真实行情，切勿用于任何实盘决策。真实回测请替换为自己的历史 CSV。
用法：python3 scripts/make_sample_data.py
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta

from config_loader import ensure_parent, resolve

# 10 个连续交易日（周一至周五），与回测样例区间一致
DATES = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
         "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]

# code, name, 基准价, 基准溢价率, 活跃度偏置
BONDS = [
    ("113050", "南银转债", 128.60, 8.5, 1.6),
    ("123138", "丝路转债", 132.45, 12.3, 1.9),
    ("128136", "立昂转债", 141.20, 18.7, 1.4),
    ("123151", "锦鸡转债", 149.80, 9.8, 2.1),
    ("127045", "牧原转债", 136.75, 15.6, 0.7),
    ("118034", "晶能转债", 112.40, 5.4, 0.3),
]

COLUMNS = ["date", "code", "name", "price", "cb_change_pct", "stock_change_pct",
           "cb_stock_gap", "volume_ratio", "premium_rate", "amount",
           "forward_return_pct"]


def main() -> None:
    random.seed(42)
    rows = []
    for d in DATES:
        # 每日共同市场冲击：模拟系统性风险，保证净值出现真实的亏损日与回撤，
        # 避免「选 Top3 把个股噪声平均掉」导致净值单调上涨的失真演示
        market = random.gauss(-0.3, 2.6)
        for code, name, base_price, base_premium, bias in BONDS:
            stock = round(random.uniform(-1.5, 6.5) + bias, 2)
            cb = round(stock * 0.38 + random.uniform(-0.8, 0.9), 2)
            gap = round(stock - cb, 2)
            vr = round(random.uniform(0.7, 2.6) + bias * 0.45, 2)
            price = round(base_price + random.uniform(-3.0, 3.0), 2)
            premium = round(base_premium + random.uniform(-1.5, 1.5), 2)
            # 未来收益 = 弱因子暴露 + 当日共同市场冲击 + 个股噪声
            fwd = round(
                0.25 * gap + 0.15 * max(stock - 3.0, 0.0)
                + 0.15 * (vr - 1.5) - 0.05 * max(premium - 15.0, 0.0)
                + market + random.gauss(0.0, 1.5), 2)
            rows.append({
                "date": d, "code": code, "name": name, "price": price,
                "cb_change_pct": cb, "stock_change_pct": stock, "cb_stock_gap": gap,
                "volume_ratio": vr, "premium_rate": premium,
                "amount": int(random.uniform(0.6, 5.2) * 1e8),
                "forward_return_pct": fwd,
            })
    out = resolve("data/sample_history.csv")
    ensure_parent(out)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"[OK] 生成 {len(rows)} 行合成历史数据 -> {out}")
    print("[注意] 该数据为演示用合成数据，非真实行情。")


if __name__ == "__main__":
    main()
