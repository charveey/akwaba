"""Règles pures des rapports : libellés de période et fusion revenus/dépenses."""
from datetime import date
from typing import Literal

Unit = Literal["month", "quarter", "year"]


def period_label(unit: Unit, start: date) -> str:
    if unit == "month":
        return f"{start.year}-{start.month:02d}"
    if unit == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    if unit == "year":
        return str(start.year)
    raise ValueError(f"unité inconnue : {unit}")


def merge_profit(
    revenue: dict[date, int], expenses: dict[date, int],
) -> list[tuple[date, int, int, int]]:
    """(début de période, revenus, dépenses, bénéfice), trié par période."""
    out = []
    for start in sorted(set(revenue) | set(expenses)):
        rev, exp = revenue.get(start, 0), expenses.get(start, 0)
        out.append((start, rev, exp, rev - exp))
    return out
