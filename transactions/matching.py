"""Recognise subscriptions (monthly/yearly expenses) in a new statement.

Statement names and prices change a little from month to month:
- banks add codes: "DL*SPOTIFY P48A3" one month, "DL*SPOTIFY Q91B2" the next;
- prices change: a subscription charged in US$ costs a different amount in R$
  every month, and services raise their prices now and then.

So a statement row matches a recurring expense when their names are the same
after removing those codes, and the amount is within MAX_PRICE_CHANGE.
"""

import re
import unicodedata
from decimal import Decimal

MAX_PRICE_CHANGE = Decimal("0.20")  # up to 20% more or less


def name_key(text):
    """'DL*SPOTIFY P48A3' -> 'spotify', 'ANTHROPIC* CLAUDE SUB  SA' -> 'anthropic claude sub'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    words = re.split(r"[^a-z0-9]+", text)
    # Drop codes (anything with a digit) and tiny words ("dl", "mp", "sa"...).
    return " ".join(w for w in words if len(w) > 2 and not any(c.isdigit() for c in w))


def match_recurring(rows, recurring):
    """Pair statement rows with the user's recurring expenses.

    rows: dicts with "description", "original_description" and "amount" (positive = charge).
    recurring: Expense objects (monthly/yearly, active in the statement's month).

    Returns {row index: (expense, price_changed)}. Each expense matches at most one
    row: the one whose amount is closest (e.g. Claude R$ 116 vs its R$ 4 IOF).
    """
    by_name = {}
    for expense in recurring:
        for name in {name_key(expense.description), name_key(expense.original_description)}:
            if name:
                by_name.setdefault(name, []).append(expense)

    # Every possible (row, expense) pair, closest amounts first.
    pairs = []
    for index, row in enumerate(rows):
        if row["amount"] <= 0:
            continue
        names = {name_key(row["description"]), name_key(row["original_description"])} - {""}
        for expense in {e for name in names for e in by_name.get(name, [])}:
            difference = abs(row["amount"] - expense.amount)
            if difference <= expense.amount * MAX_PRICE_CHANGE:
                pairs.append((difference, index, expense))
    pairs.sort(key=lambda pair: pair[0])

    matches, used = {}, set()
    for difference, index, expense in pairs:
        if index in matches or expense.pk in used:
            continue
        matches[index] = (expense, difference != 0)
        used.add(expense.pk)
    return matches
