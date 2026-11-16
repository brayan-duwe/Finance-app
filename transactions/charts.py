"""Turn a month's totals into an SVG donut chart.

The donut is a circle with a circumference of 100 (r = 15.9155), so each
segment's length is just its percentage. The light "track" underneath shows
what's left of the income.
"""

from decimal import Decimal

from django.db.models import Sum

from .models import Expense, Income, add_months

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
            "category_id": row["category"].pk,
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


def build_trend(user, last_month, months=12):
    """Monthly totals for the line chart: the `months` months up to `last_month`.

    Returns plain numbers (floats) so it can go straight into the page as JSON:
    {"months": [{"month": "2026-11", "income": 2250.0, "expenses": 1195.96,
                 "categories": {"12": 391.72, ...}}, ...]}
    The browser shows the last 3, 6 or 12 of them, and per-category lines.
    """
    data = []
    for offset in range(months - 1, -1, -1):
        month = add_months(last_month, -offset)
        incomes = Income.objects.filter(user=user).for_month(month.year, month.month)
        expenses = Expense.objects.filter(user=user).for_month(month.year, month.month)
        by_category = expenses.values("category").annotate(total=Sum("amount"))
        data.append({
            "month": f"{month:%Y-%m}",
            "income": float(incomes.aggregate(total=Sum("amount"))["total"] or 0),
            "expenses": float(sum(row["total"] for row in by_category)),
            "categories": {str(row["category"]): float(row["total"]) for row in by_category},
        })
    return {"months": data}
