"""Turn a month's totals into an SVG donut chart.

The donut is a circle with a circumference of 100 (r = 15.9155), so each
segment's length is just its percentage. The light "track" underneath shows
what's left of the income.
"""

from decimal import Decimal

GAP = 0.6  # space between segments, in percent of the circle


def build_donut(income_total, by_category):
    """by_category: [{"category": Category, "total": Decimal}, ...], largest first."""
    expense_total = sum((row["total"] for row in by_category), Decimal("0"))
    base = max(income_total, expense_total)
    if base <= 0:
        return None

    segments = []
    offset = 0.0
    for row in by_category:
        share = float(row["total"] / base * 100)
        visible = share - GAP if share > GAP * 2 else share
        segments.append({
            "label": row["category"].label,
            "color": row["category"].color,
            "amount": row["total"],
            "percent": round(float(row["total"] / income_total * 100)) if income_total else None,
            "share": round(float(row["total"] / expense_total * 100)) if expense_total else 0,  # of spending
            "items": row.get("items", []),
            "dasharray": f"{visible:.3f} {100 - visible:.3f}",
            # Start at 12 o'clock (offset 25) and go clockwise.
            "dashoffset": f"{25 - offset:.3f}",
        })
        offset += share

    left = income_total - expense_total
    return {
        "segments": segments,
        "income": income_total,
        "spent": expense_total,
        "left": left,
        "over_budget": left < 0,
        "over_by": -left if left < 0 else Decimal("0"),
        "spent_percent": round(float(expense_total / income_total * 100)) if income_total else None,
        "left_percent": round(float(left / income_total * 100)) if income_total and left > 0 else 0,
    }
