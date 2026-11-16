"""The AI assistant: answers questions about the user's finances with Claude.

How it works:
- Claude gets a short set of *tools*: small functions below that read the user's
  data (month summary, search, trend, upcoming bills). Claude decides which to call,
  we run them for the logged-in user only, and send the results back.
- No tool changes data by itself. To add an expense or income, Claude creates a
  *draft*; the chat shows it as a card, and it's only saved when the user clicks Add.
- The conversation is kept in the user's session, so follow-up questions work.

Needs ANTHROPIC_API_KEY in .env. Without it, `is_configured()` is False and the
dashboard explains how to turn the assistant on.
"""

import calendar
import datetime as dt
import json
import logging
import os
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import F, Sum
from django.urls import reverse
from django.utils import formats, translation

from .categorize import guess_category
from .currencies import format_money, get_currency
from .models import (
    OTHER, AssistantUsage, Category, DefaultIncomeCategory, Expense, Frequency, Income, Kind, add_months,
    get_category, get_profile,
)

logger = logging.getLogger(__name__)

SESSION_KEY = "assistant_messages"  # the conversation as sent to the API
DISPLAY_KEY = "assistant_display"  # what the chat shows: [{"role": "user"|"assistant", "text": ..., "drafts": [...]}]
DRAFTS_KEY = "assistant_drafts"  # drafts waiting for the user: {draft id: {...}}
NOTES_KEY = "assistant_notes"  # what happened to drafts, told to Claude with the next question
MAX_TOOL_ROUNDS = 6  # safety limit for one question
MAX_STORED_MESSAGES = 60  # past this, a new conversation starts

LANGUAGE_NAMES = {"en": "English", "pt-br": "Brazilian Portuguese", "es": "Spanish"}


class AssistantError(Exception):
    """Something went wrong; the message is shown to the user."""


def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def get_client():
    """The Anthropic client (a function, so tests can replace it with a fake)."""
    import anthropic

    return anthropic.Anthropic()


# --- Tools: what Claude can look up ------------------------------------------

def _tool(name, description, properties):
    """A tool definition with a strict schema: every field required (nullable where optional)."""
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


MONTH = {"type": ["string", "null"], "description": "A month as YYYY-MM, or null for the current month."}

TOOLS = [
    _tool(
        "month_summary",
        "Income, expenses, what's left, and spending per category for one month. "
        "Use it for questions about a month's budget, and before judging whether a purchase fits.",
        {"month": MONTH},
    ),
    _tool(
        "search_transactions",
        "Find expenses and/or incomes by text (matches the name and the original statement name), "
        "category and months. Recurring and installment items appear once per month they count in. "
        "Returns the matching items (newest first, up to `limit`), plus their count and total.",
        {
            "text": {"type": ["string", "null"], "description": "Words to look for, e.g. 'uber'. Null for any."},
            "category": {"type": ["string", "null"], "description": "Exact category name from list_categories, or null."},
            "kind": {"type": "string", "enum": ["expense", "income", "any"]},
            "month_from": {"type": ["string", "null"], "description": "First month YYYY-MM (null: 5 months before month_to)."},
            "month_to": {"type": ["string", "null"], "description": "Last month YYYY-MM (null: the current month)."},
            "limit": {"type": "integer", "description": "How many items to list (1-50). The total covers all matches."},
        },
    ),
    _tool(
        "spending_trend",
        "Monthly income and expense totals for the last N months up to the current month, optionally for "
        "one category. Use it to compare months and spot categories that are growing.",
        {
            "months": {"type": "integer", "description": "How many months (2-24)."},
            "category": {"type": ["string", "null"], "description": "Exact category name, or null for all spending."},
        },
    ),
    _tool(
        "upcoming_commitments",
        "Money already committed for the coming months: monthly and yearly bills, and the remaining "
        "installments of purchases. Use it before saying whether a new purchase or installment plan fits.",
        {"months_ahead": {"type": "integer", "description": "How many months to look ahead (1-12), starting next month."}},
    ),
    _tool(
        "list_categories",
        "The user's expense and income categories (their own names).",
        {},
    ),
]

DRAFT_FIELDS = {
    "description": {"type": "string", "description": "Short name, e.g. 'Pizza' or 'Freelance website'."},
    "amount": {"type": "number", "description": "The amount, positive (for installments: the value of each one)."},
    "date": {"type": ["string", "null"], "description": "YYYY-MM-DD (work out 'yesterday' etc. from today's date), or null for today."},
    "category": {"type": ["string", "null"], "description": "Exact category name from list_categories, or null to choose automatically."},
}
TOOLS += [
    _tool(
        "draft_expense",
        "Prepare an expense to add. It is NOT saved yet: the user sees it as a card in the chat and saves it "
        "by clicking Add (or cancels). Use one call per expense.",
        {
            **DRAFT_FIELDS,
            "repeats": {"type": "string", "enum": ["once", "monthly", "yearly", "installments"]},
            "installments": {"type": ["integer", "null"], "description": "Number of installments (2-120) when repeats is 'installments', else null."},
        },
    ),
    _tool(
        "draft_income",
        "Prepare an income to add (salary, freelance, gift...). It is NOT saved yet: the user sees it as a "
        "card in the chat and saves it by clicking Add (or cancels).",
        {**DRAFT_FIELDS, "repeats": {"type": "string", "enum": ["once", "monthly", "yearly"]}},
    ),
    _tool(
        "show_transactions",
        "Show transactions to the user as clickable cards in the chat (each with Open and Delete buttons). "
        "Use the ids and kinds from search_transactions. At most 10.",
        {
            "items": {
                "type": "array",
                "description": "The transactions to show.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["expense", "income"]},
                        "id": {"type": "integer"},
                    },
                    "required": ["kind", "id"],
                    "additionalProperties": False,
                },
            },
        },
    ),
    _tool(
        "propose_change",
        "Propose editing or deleting ONE existing transaction (id and kind from search_transactions). Nothing "
        "changes yet: the user sees a card with the change (before -> after) and must click Apply or Delete. "
        "For an edit, give only the fields that change; use null for the others.",
        {
            "kind": {"type": "string", "enum": ["expense", "income"]},
            "id": {"type": "integer"},
            "action": {"type": "string", "enum": ["edit", "delete"]},
            "description": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "date": {"type": ["string", "null"], "description": "YYYY-MM-DD, or null."},
            "category": {"type": ["string", "null"], "description": "Exact category name, or null."},
            "repeats": {"type": "string", "enum": ["unchanged", "once", "monthly", "yearly"],
                        "description": "'unchanged' unless the user wants to change how it repeats."},
        },
    ),
]


def _parse_month(text, default):
    try:
        year, month = map(int, (text or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return default


def _money(value):
    return float(Decimal(value).quantize(Decimal("0.01")))


def _entries_in_month(model, user, month):
    return model.objects.filter(user=user).for_month(month.year, month.month).select_related("category")


class Tools:
    """The tool functions, always scoped to one user."""

    def __init__(self, user, today=None):
        self.user = user
        self.today = today or date.today()
        self.this_month = self.today.replace(day=1)
        self.drafts = []  # drafts created while answering this question

    def run(self, name, arguments):
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            raise AssistantError(f"Unknown tool: {name}")
        return method(**arguments)

    def tool_draft_expense(self, description, amount, date, category, repeats, installments):
        return self._draft(Kind.EXPENSE, description, amount, date, category, repeats, installments)

    def tool_draft_income(self, description, amount, date, category, repeats):
        return self._draft(Kind.INCOME, description, amount, date, category, repeats, None)

    def _draft(self, kind, description, amount, when, category, repeats, installments):
        """Check the values and keep them as a draft. Errors go back to Claude, who can fix or ask."""
        description = (description or "").strip()[:200]
        if not description:
            raise ValueError("The description is empty.")
        try:
            amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ValueError("The amount isn't a number.") from exc
        if amount <= 0:
            raise ValueError("The amount must be greater than zero.")
        try:
            when = date.fromisoformat(when) if when else self.today
        except ValueError as exc:
            raise ValueError("The date must be YYYY-MM-DD.") from exc
        if repeats not in Frequency.values or (kind == Kind.INCOME and repeats == Frequency.INSTALLMENTS):
            raise ValueError(f"Invalid 'repeats': {repeats}.")
        if repeats == Frequency.INSTALLMENTS:
            if not installments or not 2 <= int(installments) <= 120:
                raise ValueError("Say how many installments (2 to 120).")
            installments = int(installments)
        else:
            installments = None

        # The category: the name Claude gave, else a guess from the description, else the default.
        categories = Category.objects.filter(user=self.user, kind=kind)
        chosen = None
        if category:
            chosen = next((c for c in categories if str(c.label).lower() == category.strip().lower()), None)
        if chosen is None and kind == Kind.EXPENSE:
            chosen = categories.filter(system_key=guess_category(description)).first()
        if chosen is None:
            default = OTHER if kind == Kind.EXPENSE else DefaultIncomeCategory.SALARY
            chosen = get_category(self.user, kind, default)

        currency = get_profile(self.user).currency
        draft = {
            "id": uuid.uuid4().hex[:12],
            "type": "new",
            "done_label": translation.gettext("Added"),
            "kind": kind,
            "kind_label": str(Kind.EXPENSE.label if kind == Kind.EXPENSE else Kind.INCOME.label),
            "description": description,
            "amount": str(amount),
            "amount_display": format_money(amount, currency),
            "date": when.isoformat(),
            "date_display": formats.date_format(when, "SHORT_DATE_FORMAT"),
            "category_id": chosen.pk,
            "category_label": str(chosen.label),
            "category_color": chosen.color,
            "repeats": repeats,
            "repeats_label": str(Frequency(repeats).label),
            "installments": installments,
            "status": "pending",
        }
        self.drafts.append(draft)
        return {
            "draft_id": draft["id"],
            "status": "waiting for the user to click Add on the card (not saved yet)",
            "saved_as": {
                "description": description, "amount": float(amount), "date": draft["date"],
                "category": draft["category_label"], "repeats": repeats, "installments": installments,
            },
        }

    # --- Finding, editing and deleting existing transactions ---

    def _entry(self, kind, entry_id):
        model = Expense if kind == Kind.EXPENSE else Income
        return model.objects.filter(user=self.user, pk=entry_id).select_related("category").first()

    def _card_fields(self, entry):
        """What a card needs to show an existing transaction."""
        currency = get_profile(self.user).currency
        return {
            "kind": entry.kind,
            "entry_id": entry.pk,
            "description": entry.description,
            "amount_display": format_money(entry.amount, currency),
            "date_display": formats.date_format(entry.date, "SHORT_DATE_FORMAT"),
            "category_label": str(entry.category.label),
            "category_color": entry.category.color,
            "repeats": entry.frequency,
            "repeats_label": str(entry.get_frequency_display()),
            "url": reverse("entry_detail", args=[entry.kind, entry.pk]),
            "delete_url": reverse("delete_entry", args=[entry.kind, entry.pk]),
        }

    def tool_show_transactions(self, items):
        shown, missing, seen = [], [], set()
        for item in items[:10]:
            key = (item["kind"], item["id"])
            if key in seen:
                continue
            seen.add(key)
            entry = self._entry(*key)
            if entry is None:
                missing.append(item["id"])
            else:
                shown.append(self._card_fields(entry))
        if shown:
            self.drafts.append({"id": uuid.uuid4().hex[:12], "type": "found", "items": shown, "status": "shown"})
        return {"shown_to_user": len(shown), "not_found_ids": missing}

    def tool_propose_change(self, kind, id, action, description, amount, date, category, repeats):
        gettext = translation.gettext
        entry = self._entry(kind, id)
        if entry is None:
            raise ValueError(f"There's no {kind} with id {id}. Search again.")

        changes, apply = [], {}
        currency = get_profile(self.user).currency
        if action == "edit":
            if description is not None and description.strip() and description.strip() != entry.description:
                apply["description"] = description.strip()[:200]
                changes.append({"label": gettext("Description"), "before": entry.description, "after": apply["description"]})
            if amount is not None:
                try:
                    new_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
                except InvalidOperation as exc:
                    raise ValueError("The amount isn't a number.") from exc
                if new_amount <= 0:
                    raise ValueError("The amount must be greater than zero.")
                if new_amount != entry.amount:
                    apply["amount"] = str(new_amount)
                    changes.append({"label": gettext("Amount"), "before": format_money(entry.amount, currency),
                                    "after": format_money(new_amount, currency)})
            if date is not None:
                try:
                    new_date = dt.date.fromisoformat(date)  # `date` here is the tool's argument
                except ValueError as exc:
                    raise ValueError("The date must be YYYY-MM-DD.") from exc
                if new_date != entry.date:
                    apply["date"] = new_date.isoformat()
                    changes.append({"label": gettext("Date"), "before": formats.date_format(entry.date, "SHORT_DATE_FORMAT"),
                                    "after": formats.date_format(new_date, "SHORT_DATE_FORMAT")})
            if category is not None:
                chosen = next((c for c in Category.objects.filter(user=self.user, kind=kind)
                               if str(c.label).lower() == category.strip().lower()), None)
                if chosen is None:
                    raise ValueError(f"There's no {kind} category called '{category}'. Use list_categories.")
                if chosen.pk != entry.category_id:
                    apply["category_id"] = chosen.pk
                    changes.append({"label": gettext("Category"), "before": str(entry.category.label), "after": str(chosen.label)})
            if repeats not in (None, "unchanged") and repeats != entry.frequency:
                if entry.frequency == Frequency.INSTALLMENTS:
                    raise ValueError("Installment purchases can't be changed to repeat differently here.")
                apply["frequency"] = repeats
                changes.append({"label": gettext("Repeats"), "before": str(entry.get_frequency_display()),
                                "after": str(Frequency(repeats).label)})
            if not changes:
                raise ValueError("Nothing would change: all the given values are the same as now.")

        card = {
            "id": uuid.uuid4().hex[:12],
            "type": "change",
            "action": action,
            **self._card_fields(entry),
            "changes": changes,
            "apply": apply,
            "recurring": entry.is_recurring,
            "status": "pending",
            "title": gettext("Edit") if action == "edit" else gettext("Delete"),
            "button_label": gettext("Apply") if action == "edit" else gettext("Delete"),
            "done_label": gettext("Updated") if action == "edit" else gettext("Deleted"),
        }
        self.drafts.append(card)
        return {
            "proposal_id": card["id"],
            "status": "waiting for the user to click the button on the card (nothing changed yet)",
            "changes": [{"field": c["label"], "from": c["before"], "to": c["after"]} for c in changes],
            "applies_to_every_month": entry.is_recurring,
        }

    def tool_list_categories(self):
        categories = Category.objects.filter(user=self.user)
        return {
            "expense_categories": [str(c.label) for c in categories if c.kind == Kind.EXPENSE],
            "income_categories": [str(c.label) for c in categories if c.kind == Kind.INCOME],
        }

    def tool_month_summary(self, month):
        month = _parse_month(month, self.this_month)
        incomes = _entries_in_month(Income, self.user, month)
        expenses = _entries_in_month(Expense, self.user, month)
        income_total = incomes.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        by_category = {}
        for expense in expenses:
            by_category[str(expense.category.label)] = by_category.get(str(expense.category.label), Decimal("0")) + expense.amount
        expense_total = sum(by_category.values(), Decimal("0"))
        return {
            "month": f"{month:%Y-%m}",
            "income": _money(income_total),
            "expenses": _money(expense_total),
            "left": _money(income_total - expense_total),
            "expenses_by_category": [
                {"category": name, "amount": _money(amount),
                 "percent_of_income": round(float(amount / income_total * 100)) if income_total else None}
                for name, amount in sorted(by_category.items(), key=lambda item: -item[1])
            ],
            "number_of_expenses": expenses.count(),
            "is_current_month": month == self.this_month,
            "days_left_in_month": (
                calendar.monthrange(month.year, month.month)[1] - date.today().day if month == self.this_month else None
            ),
        }

    def tool_search_transactions(self, text, category, kind, month_from, month_to, limit):
        last = _parse_month(month_to, self.this_month)
        first = _parse_month(month_from, add_months(last, -5))
        if first > last:
            first, last = last, first
        first = max(first, add_months(last, -23))  # at most 24 months
        limit = max(1, min(int(limit or 20), 50))
        words = (text or "").lower().split()
        wanted_category = (category or "").strip().lower()
        models = {"expense": [Expense], "income": [Income], "any": [Expense, Income]}[kind]

        items = []
        month = first
        while month <= last:
            for model in models:
                for entry in _entries_in_month(model, self.user, month):
                    haystack = f"{entry.description} {getattr(entry, 'original_description', '')}".lower()
                    if words and not all(word in haystack for word in words):
                        continue
                    if wanted_category and str(entry.category.label).lower() != wanted_category:
                        continue
                    shown = entry.date_in_month(month.year, month.month)
                    item = {
                        "id": entry.pk,
                        "kind": entry.kind,
                        "date": shown.isoformat(),
                        "description": entry.description,
                        "amount": _money(entry.amount),
                        "category": str(entry.category.label),
                        "repeats": entry.frequency,
                    }
                    if getattr(entry, "original_description", "") and entry.original_description != entry.description:
                        item["name_on_statement"] = entry.original_description
                    installment = entry.installment_in_month(month.year, month.month)
                    if installment:
                        item["installment"] = f"{installment}/{entry.installment_total}"
                    items.append(item)
            month = add_months(month, 1)

        items.sort(key=lambda item: item["date"], reverse=True)
        return {
            "months": f"{first:%Y-%m} to {last:%Y-%m}",
            "count": len(items),
            "total_expenses": _money(sum((Decimal(str(i["amount"])) for i in items if i["kind"] == "expense"), Decimal("0"))),
            "total_income": _money(sum((Decimal(str(i["amount"])) for i in items if i["kind"] == "income"), Decimal("0"))),
            "items": items[:limit],
            "more_not_listed": max(0, len(items) - limit),
        }

    def tool_spending_trend(self, months, category):
        months = max(2, min(int(months or 6), 24))
        wanted = (category or "").strip().lower()
        result = []
        for offset in range(months - 1, -1, -1):
            month = add_months(self.this_month, -offset)
            expenses = _entries_in_month(Expense, self.user, month)
            if wanted:
                expenses = [e for e in expenses if str(e.category.label).lower() == wanted]
            income = _entries_in_month(Income, self.user, month).aggregate(total=Sum("amount"))["total"] or 0
            result.append({
                "month": f"{month:%Y-%m}",
                "expenses": _money(sum((e.amount for e in expenses), Decimal("0"))),
                "income": _money(income),
            })
        return {"category": category or "all spending", "months": result}

    def tool_upcoming_commitments(self, months_ahead):
        months_ahead = max(1, min(int(months_ahead or 3), 12))
        result = []
        for offset in range(1, months_ahead + 1):
            month = add_months(self.this_month, offset)
            bills, installments = [], []
            for expense in _entries_in_month(Expense, self.user, month):
                if expense.frequency in (Frequency.MONTHLY, Frequency.YEARLY):
                    bills.append({"description": expense.description, "amount": _money(expense.amount), "repeats": expense.frequency})
                elif expense.frequency == Frequency.INSTALLMENTS:
                    number = expense.installment_in_month(month.year, month.month)
                    installments.append({
                        "description": expense.description, "amount": _money(expense.amount),
                        "installment": f"{number}/{expense.installment_total}",
                        "installments_left_after_this": expense.installment_total - number,
                    })
            income = _entries_in_month(Income, self.user, month).filter(
                frequency__in=[Frequency.MONTHLY, Frequency.YEARLY]
            ).aggregate(total=Sum("amount"))["total"] or 0
            committed = sum(i["amount"] for i in bills + installments)
            result.append({
                "month": f"{month:%Y-%m}",
                "recurring_income": _money(income),
                "committed_total": _money(committed),
                "free_after_commitments": _money(Decimal(str(income)) - Decimal(str(committed))),
                "bills": bills,
                "installments": installments,
            })
        return {"months": result}


# --- Instructions ------------------------------------------------------------

def system_prompt(user, today=None):
    """Stable instructions first (cacheable), then today's facts about the user."""
    today = today or date.today()
    profile = get_profile(user)
    currency = get_currency(profile.currency)
    example = f"{currency['symbol']}{' ' if currency['space'] else ''}1{currency['thousands']}234{currency['decimal']}56"
    return f"""You are the assistant inside a personal finance app. The user tracks their income and expenses here: \
credit card statements they import, expenses and incomes they add, monthly subscriptions and purchases in installments.

You help them understand and improve their money: answer questions about their spending, find transactions, \
compare months, say whether a purchase fits their budget, and suggest concrete ways to save.

How to work:
- Get facts from the tools. Never guess or invent amounts, dates or transactions; if the data isn't there, say so.
- Before saying whether a purchase fits, check this month's summary and the upcoming commitments (bills and installments).
- When suggesting savings, point to the user's real categories and transactions, with amounts, and keep it practical.
- To add an expense or income, use draft_expense or draft_income (one call per item). A draft is NOT saved: \
the user sees a card in the chat and clicks Add to save it, or Cancel. So never say it was added or saved; after \
drafting, tell them in one short sentence to check the card and click Add. If the amount or what it is isn't clear, \
ask first instead of guessing. Use "installments" only when the user mentions them, and give the value of each \
installment as the amount (divide a total if needed, and say so).
- To find transactions, use search_transactions, then show_transactions with the ones the user is looking for: \
they appear as cards with Open and Delete buttons. Show them instead of listing them in text.
- To edit or delete one, use propose_change (one call per transaction). Nothing changes until the user clicks the \
button on the card, so never say it's done. If more than one transaction could match, show them and ask which one. \
Editing or deleting a monthly, yearly or installment item affects every month it counts in: say so.
- You give general guidance, not professional financial advice. Say this only when giving bigger financial \
recommendations (investments, debt), in one short sentence.

How to write:
- Reply in {LANGUAGE_NAMES.get(profile.language, "English")}, the user's chosen language, unless they write in another one.
- Be brief and friendly: usually 1 to 4 short sentences. Use a short list (lines starting with "- ") only for several items.
- Write amounts in the user's currency format, like {example}. Use **bold** only for the key number of an answer.
- No headings, tables or code.

Today is {today:%A, %d %B %Y}. The user's currency is {profile.currency}."""


# --- The conversation ----------------------------------------------------------

def _text_of(content):
    return "\n\n".join(block["text"] for block in content if block.get("type") == "text" and block.get("text")).strip()


def ask(request, question, client=None, today=None):
    """Answer one question. Returns (reply text, drafts to show as cards).

    The conversation is kept in the session. Drafts the user confirmed or cancelled
    since the last question are mentioned to Claude, so it knows what was saved.
    """
    question = (question or "").strip()[:1000]
    if not question:
        raise AssistantError(translation.gettext("Type a question first."))

    messages = list(request.session.get(SESSION_KEY, []))
    display = list(request.session.get(DISPLAY_KEY, []))
    if len(messages) > MAX_STORED_MESSAGES:
        messages, display = [], []  # start fresh instead of editing old history

    count_question(request.user)  # stops here if the daily limit is reached

    notes = request.session.pop(NOTES_KEY, [])
    sent = question if not notes else "[Note from the app: " + " ".join(notes) + "]\n\n" + question
    messages.append({"role": "user", "content": sent})
    display.append({"role": "user", "text": question})

    client = client or get_client()
    tools = Tools(request.user, today=today)
    system = system_prompt(request.user, today=today)
    reply = ""

    for _round in range(MAX_TOOL_ROUNDS):
        response = _call_claude(client, system, messages)
        content = response.to_dict()["content"]
        # Keep Claude's whole reply (including thinking blocks) unchanged in the history.
        messages.append({"role": "assistant", "content": content})

        if response.stop_reason == "refusal":
            reply = translation.gettext("Sorry, I can't help with that one.")
            break
        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [block for block in content if block.get("type") == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            reply = _text_of(content)
            break

        results = []
        for block in tool_uses:
            try:
                output = tools.run(block["name"], block.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(output, ensure_ascii=False)})
            except Exception as exc:  # report the problem to Claude instead of failing the whole answer
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": f"Error: {exc}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        reply = _text_of(messages[-1]["content"]) if messages[-1]["role"] == "assistant" else ""

    if not reply:
        reply = (translation.gettext("Check the card below and click Add to save it.") if tools.drafts
                 else translation.gettext("Sorry, I couldn't find an answer. Could you ask in another way?"))

    display.append({"role": "assistant", "text": reply, "drafts": tools.drafts})
    drafts = dict(request.session.get(DRAFTS_KEY, {}))
    drafts.update({draft["id"]: draft for draft in tools.drafts if draft["type"] in ("new", "change")})
    request.session[SESSION_KEY] = messages
    request.session[DISPLAY_KEY] = display
    request.session[DRAFTS_KEY] = drafts
    return reply, tools.drafts


def resolve_draft(request, draft_id, confirm):
    """The user clicked the main button (Add / Apply / Delete) or Cancel on a card.

    Returns (draft, message for a notification, or None when cancelled).
    """
    gettext = translation.gettext
    drafts = dict(request.session.get(DRAFTS_KEY, {}))
    draft = drafts.get(draft_id)
    if not draft:
        raise AssistantError(gettext("This suggestion has expired. Ask the assistant again."))
    if draft["status"] != "pending":
        raise AssistantError(gettext("This suggestion was already handled."))

    message = None
    if confirm and draft.get("type", "new") == "new":
        model = Expense if draft["kind"] == Kind.EXPENSE else Income
        category = (Category.objects.filter(user=request.user, kind=draft["kind"], pk=draft["category_id"]).first()
                    or get_category(request.user, draft["kind"], OTHER))  # the category may have been deleted
        model.objects.create(
            user=request.user,
            description=draft["description"],
            amount=Decimal(draft["amount"]),
            date=date.fromisoformat(draft["date"]),
            category=category,
            frequency=draft["repeats"],
            installment_total=draft["installments"],
        )
        message = gettext("Added “%(name)s”.") % {"name": draft["description"]}
    elif confirm:  # a proposed edit or delete of an existing transaction
        model = Expense if draft["kind"] == Kind.EXPENSE else Income
        entry = model.objects.filter(user=request.user, pk=draft["entry_id"]).first()
        if entry is None:
            raise AssistantError(gettext("This transaction no longer exists."))
        if draft["action"] == "delete":
            entry.delete()
            message = gettext("Deleted “%(name)s”.") % {"name": entry.description}
        else:
            changes = draft["apply"]
            if "description" in changes:
                entry.description = changes["description"]
            if "amount" in changes:
                entry.amount = Decimal(changes["amount"])
            if "date" in changes:
                entry.date = date.fromisoformat(changes["date"])
            if "category_id" in changes:
                entry.category = Category.objects.filter(
                    user=request.user, kind=draft["kind"], pk=changes["category_id"]
                ).first() or entry.category
            if "frequency" in changes:
                entry.frequency = changes["frequency"]
            entry.save()
            message = gettext("Saved “%(name)s”.") % {"name": entry.description}
    draft["status"] = "confirmed" if confirm else "cancelled"
    drafts[draft_id] = draft

    # Show the new state if the chat is reloaded, and tell Claude with the next question.
    display = list(request.session.get(DISPLAY_KEY, []))
    for item in display:
        for shown in item.get("drafts", []):
            if shown["id"] == draft_id:
                shown["status"] = draft["status"]
    notes = list(request.session.get(NOTES_KEY, []))
    if draft.get("type", "new") == "new":
        action = "saved" if confirm else "cancelled"
        notes.append(f"The user {action} draft {draft_id} ({draft['description']}, {draft['amount']}).")
    else:
        done = {"edit": "applied", "delete": "confirmed"}[draft["action"]] if confirm else "cancelled"
        notes.append(f"The user {done} the proposed {draft['action']} of {draft['kind']} {draft['entry_id']} "
                     f"({draft['description']}).")

    request.session[DRAFTS_KEY] = drafts
    request.session[DISPLAY_KEY] = display
    request.session[NOTES_KEY] = notes
    return draft, message


# Models that support the server-side refusal fallback. Others (e.g. Haiku) are called without it.
MODELS_WITH_FALLBACK = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def _call_claude(client, system, messages):
    import anthropic

    options = {}
    if settings.AI_MODEL in MODELS_WITH_FALLBACK:
        # If the model declines a request for safety reasons, retry it on a fallback model.
        options = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        return client.beta.messages.create(
            model=settings.AI_MODEL,
            max_tokens=8000,
            system=system,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": settings.AI_EFFORT},
            # Reuse the instructions and tools between questions (cheaper and faster).
            cache_control={"type": "ephemeral"},
            **options,
        )
    except anthropic.AuthenticationError as exc:
        raise AssistantError(translation.gettext("The API key isn't valid. Check ANTHROPIC_API_KEY in .env.")) from exc
    except anthropic.RateLimitError as exc:
        raise AssistantError(translation.gettext("Too many questions right now. Try again in a minute.")) from exc
    except anthropic.APIConnectionError as exc:
        raise AssistantError(translation.gettext("Couldn't reach the assistant. Check your internet connection.")) from exc
    except anthropic.APIStatusError as exc:
        # Log the real reason (visible in the terminal running the server). While developing
        # (DEBUG=True), also show it in the chat, so problems like a bad tool schema are obvious.
        logger.warning("Claude API error %s: %s", exc.status_code, exc.message)
        message = translation.gettext("The assistant had a problem. Try again in a moment.")
        if settings.DEBUG:
            message += f" ({exc.status_code}: {exc.message})"
        raise AssistantError(message) from exc


def count_question(user):
    """Count one question for today, or refuse if the user already reached AI_DAILY_LIMIT.

    Staff accounts (the site owner) have no limit, and AI_DAILY_LIMIT=0 turns the limit off.
    """
    limit = settings.AI_DAILY_LIMIT
    if user.is_staff or limit <= 0:
        return
    usage, _created = AssistantUsage.objects.get_or_create(user=user, day=date.today())
    if usage.questions >= limit:
        raise AssistantError(translation.ngettext(
            "You've reached today's limit of %(limit)d question. Come back tomorrow!",
            "You've reached today's limit of %(limit)d questions. Come back tomorrow!",
            limit,
        ) % {"limit": limit})
    # Counted in the database itself, so two questions at the same time can't both slip through.
    AssistantUsage.objects.filter(pk=usage.pk).update(questions=F("questions") + 1)


def reset(request):
    for key in (SESSION_KEY, DISPLAY_KEY, DRAFTS_KEY, NOTES_KEY):
        request.session.pop(key, None)
