from datetime import date
from decimal import Decimal
from itertools import chain

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from .categorize import guess_category
from .charts import build_donut
from .forms import (
    BillMonthForm, ExpenseForm, ImportRowFormSet, IncomeForm, ProfileForm, StatementUploadForm,
)
from .models import Category, Expense, Frequency, Income, add_months, get_profile
from .parsers import StatementError, parse_statement
from .refunds import pair_refunds

IMPORT_SESSION_KEY = "statement_import"
ENTRY_MODELS = {"income": Income, "expense": Expense}


# --- Accounts and settings -------------------------------------------------


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        profile = get_profile(user)
        profile.language = request.LANGUAGE_CODE  # keep the language picked on the sign-up page
        profile.save()
        login(request, user)
        messages.success(request, _("Welcome! Start by adding your salary."))
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def user_settings(request):
    profile = get_profile(request.user)
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        translation.activate(profile.language)  # so this message is already in the new language
        messages.success(request, _("Settings saved."))
        response = redirect("user_settings")
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, profile.language, max_age=365 * 24 * 3600)
        return response
    return render(request, "transactions/settings.html", {"form": form})


# --- Dashboard -------------------------------------------------------------


def _selected_month(request):
    """The month from ?month=2026-11, or the current month."""
    try:
        year, month = map(int, request.GET.get("month", "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return date.today().replace(day=1)


def _dashboard_url(day):
    return f"{reverse('dashboard')}?month={day:%Y-%m}"


def _safe_next(request, url):
    """Only follow ?next= links that stay on this site."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return None


@login_required
def dashboard(request):
    month = _selected_month(request)
    incomes = Income.objects.filter(user=request.user).for_month(month.year, month.month)
    expenses = Expense.objects.filter(user=request.user).for_month(month.year, month.month)

    income_total = incomes.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    by_category = list(expenses.values("category").annotate(total=Sum("amount")).order_by("-total"))
    chart = build_donut(income_total, by_category)

    entries = list(chain(incomes, expenses))
    for entry in entries:
        entry.shown_date = entry.date_in_month(month.year, month.month)
        entry.installment_now = entry.installment_in_month(month.year, month.month)
    entries.sort(key=lambda e: (e.shown_date, e.created_at), reverse=True)

    return render(request, "transactions/dashboard.html", {
        "month": month,
        "prev_month": add_months(month, -1),
        "next_month": add_months(month, 1),
        "is_current_month": month == date.today().replace(day=1),
        "income_total": income_total,
        "chart": chart,
        "entries": entries,
    })


# --- Add / view / edit / delete --------------------------------------------


def _entry_form(request, form_class, title, initial):
    form = form_class(request.POST or None, initial={"date": date.today(), **initial})
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.user = request.user
        entry.save()
        messages.success(request, _("Added “%(name)s”.") % {"name": entry.description})
        return redirect(_dashboard_url(entry.date if not entry.is_recurring else date.today()))
    return render(request, "transactions/entry_form.html", {"form": form, "title": title})


@login_required
def add_expense(request):
    return _entry_form(request, ExpenseForm, _("Add an expense"), {})


@login_required
def add_income(request):
    has_income = Income.objects.filter(user=request.user).exists()
    initial = {} if has_income else {"description": _("Salary"), "frequency": Frequency.MONTHLY}
    return _entry_form(request, IncomeForm, _("Add an income"), initial)


@login_required
def entry_detail(request, kind, pk):
    """Details of an income or expense, with a form to edit it."""
    if kind not in ENTRY_MODELS:
        raise Http404
    entry = get_object_or_404(ENTRY_MODELS[kind], pk=pk, user=request.user)
    form_class = ExpenseForm if kind == "expense" else IncomeForm
    form = form_class(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        if kind == "expense" and not entry.imported:
            entry.first_installment_month = None  # recalculated from the (maybe changed) date
        form.save()
        messages.success(request, _("Saved “%(name)s”.") % {"name": entry.description})
        return redirect(_safe_next(request, request.POST.get("next")) or "dashboard")

    today = date.today()
    return render(request, "transactions/entry_detail.html", {
        "entry": entry,
        "form": form,
        "installment_now": entry.installment_in_month(today.year, today.month),
        "total_amount": entry.amount * entry.installment_total if entry.installment_total else None,
        "next": _safe_next(request, request.GET.get("next")) or reverse("dashboard"),
    })


@require_POST
@login_required
def delete_entry(request, kind, pk):
    if kind not in ENTRY_MODELS:
        raise Http404
    entry = get_object_or_404(ENTRY_MODELS[kind], pk=pk, user=request.user)
    entry.delete()
    messages.success(request, _("Deleted “%(name)s”.") % {"name": entry.description})
    next_url = _safe_next(request, request.POST.get("next"))
    if next_url and reverse("entry_detail", args=[kind, pk]) in next_url:
        next_url = None  # don't go back to the page of the entry we just deleted
    return redirect(next_url or "dashboard")


# --- Statement import ------------------------------------------------------


@login_required
def import_statement(request):
    form = StatementUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            rows = parse_statement(
                upload.name,
                upload.read(),
                day_first=form.cleaned_data["date_order"] == "dmy",
                currency=get_profile(request.user).currency,
            )
        except StatementError as exc:
            form.add_error("file", str(exc))
        else:
            if not rows:
                form.add_error("file", _(
                    "No transactions were found in this file. Try your bank's CSV export, "
                    "or check the date format and the currency in Settings."
                ))
            else:
                request.session[IMPORT_SESSION_KEY] = {
                    "filename": upload.name,
                    # Most statements end on the purchases of the month the bill is paid.
                    "bill_month": max(r.date for r in rows).replace(day=1).isoformat(),
                    "rows": [
                        {
                            "date": r.date.isoformat(),
                            "description": r.description[:200],
                            "original": r.original[:200],
                            "amount": str(r.amount),
                            "bank_category": r.bank_category,
                            "installment_number": r.installment_number,
                            "installment_total": r.installment_total,
                        }
                        for r in rows
                    ],
                }
                return redirect("import_review")
    return render(request, "transactions/import_upload.html", {"form": form})


def _review_rows(user, rows):
    """Initial data for the review table.

    Unchecked from the start: duplicates, bill payments, and refunds together with
    the charge they cancel.
    """
    dates = [date.fromisoformat(r["date"]) for r in rows]
    existing = Expense.objects.filter(user=user)

    def key(expense_or_row_name):
        return expense_or_row_name.strip().lower()

    # One-time purchases and installment plans already added (same date, amount and statement name).
    already_added = {
        (d, amount, key(original or description))
        for d, amount, original, description in existing.filter(
            frequency__in=[Frequency.ONCE, Frequency.INSTALLMENTS],
            date__gte=min(dates), date__lte=max(dates),
        ).values_list("date", "amount", "original_description", "description")
    }
    # Monthly / yearly bills already counted every month.
    recurring = set()
    for amount, original, description in existing.filter(
        frequency__in=[Frequency.MONTHLY, Frequency.YEARLY]
    ).values_list("amount", "original_description", "description"):
        recurring.add((amount, key(description)))
        if original:
            recurring.add((amount, key(original)))

    initial = []
    for r in rows:
        row_date, amount = date.fromisoformat(r["date"]), Decimal(r["amount"])
        names = {key(r["original"]), key(r["description"])}
        category = guess_category(r["description"], r.get("bank_category", ""))
        duplicate = any((row_date, amount, n) in already_added for n in names)
        counted_as_recurring = any((amount, n) in recurring for n in names)
        is_credit = amount <= 0

        if r.get("installment_total"):
            frequency = Frequency.INSTALLMENTS
        elif category == Category.SUBSCRIPTIONS:
            frequency = Frequency.MONTHLY
        else:
            frequency = Frequency.ONCE

        initial.append({
            "include": not (duplicate or counted_as_recurring or is_credit),
            "date": row_date,
            "description": r["description"],
            "original_description": r["original"],
            "amount": amount,
            "category": category,
            "frequency": frequency,
            "installment_number": r.get("installment_number"),
            "installment_total": r.get("installment_total"),
            # Shown as notes in the table:
            "duplicate": duplicate,
            "counted_as_recurring": counted_as_recurring,
            "is_credit": is_credit,
        })
    # A refund and the charge it cancels (e.g. a fee and its "estorno") both stay out.
    return pair_refunds(initial)


@login_required
def import_review(request):
    data = request.session.get(IMPORT_SESSION_KEY)
    if not data:
        messages.info(request, _("Upload a statement first."))
        return redirect("import_statement")

    initial = _review_rows(request.user, data["rows"])
    month_initial = {"bill_month": date.fromisoformat(data["bill_month"])}

    if request.method == "POST":
        formset = ImportRowFormSet(request.POST, initial=initial)
        month_form = BillMonthForm(request.POST, initial=month_initial)
        if formset.is_valid() and month_form.is_valid():
            bill_month = month_form.cleaned_data["bill_month"].replace(day=1)
            new_expenses = []
            for f in formset:
                row = f.cleaned_data
                if not row.get("include") or row["amount"] <= 0:
                    continue
                expense = Expense(
                    user=request.user,
                    description=row["description"],
                    original_description=row["original_description"],
                    amount=row["amount"],
                    date=row["date"],
                    category=row["category"],
                    frequency=row["frequency"],
                    source_file=data["filename"],
                    imported=True,
                )
                if row["frequency"] == Frequency.INSTALLMENTS:
                    # Installment 3 of 4 on this bill -> installment 1 was 2 months earlier.
                    expense.installment_total = row["installment_total"]
                    expense.first_installment_month = add_months(bill_month, -(row["installment_number"] - 1))
                expense.save()
                new_expenses.append(expense)

            del request.session[IMPORT_SESSION_KEY]
            count = len(new_expenses)
            messages.success(request, ngettext(
                "Added %(count)d expense from %(file)s.",
                "Added %(count)d expenses from %(file)s.",
                count,
            ) % {"count": count, "file": data["filename"]})
            return redirect(_dashboard_url(bill_month))
    else:
        formset = ImportRowFormSet(initial=initial)
        month_form = BillMonthForm(initial=month_initial)

    return render(request, "transactions/import_review.html", {
        "formset": formset,
        "month_form": month_form,
        "filename": data["filename"],
    })


@require_POST
@login_required
def import_cancel(request):
    request.session.pop(IMPORT_SESSION_KEY, None)
    return redirect("dashboard")
