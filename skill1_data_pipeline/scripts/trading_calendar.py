"""交易日历：法定休市跳过 + T-1 跨节假日自动找最近交易日。

- 周末默认休市；
- 法定休市日清单由 config.json 的 calendar.holidays 提供（可配置，不写死年份）；
- 零第三方依赖，纯标准库。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def holidays_from_config(cfg: dict) -> set[str]:
    """从配置读取休市日（YYYY-MM-DD 字符串列表）。"""
    return set(cfg.get("calendar", {}).get("holidays", []) or [])


def is_trading_day(day: str | date, holidays: Iterable[str] | None = None) -> bool:
    d = parse_date(day)
    closed = set(holidays or [])
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False
    return d.isoformat() not in closed


def prev_trading_day(day: str | date, holidays: Iterable[str] | None = None,
                     max_lookback: int = 15) -> date:
    """向前找最近一个交易日，用于 T-1 跨节假日 / 跨周末计算。"""
    d = parse_date(day)
    for _ in range(max_lookback):
        d -= timedelta(days=1)
        if is_trading_day(d, holidays):
            return d
    raise ValueError(f"{max_lookback} 天内未找到交易日，请检查 calendar.holidays 配置")


def next_trading_day(day: str | date, holidays: Iterable[str] | None = None,
                     max_lookahead: int = 15) -> date:
    d = parse_date(day)
    for _ in range(max_lookahead):
        d += timedelta(days=1)
        if is_trading_day(d, holidays):
            return d
    raise ValueError(f"{max_lookahead} 天内未找到交易日，请检查 calendar.holidays 配置")
