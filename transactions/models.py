import calendar
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .currencies import currency_choices


def add_months(day, months):
    """The first day of the month `months` after (or before) `day`'s month."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def months_between(start, end):
    return (end.year - start.year) * 12 + end.month - start.month


class Frequency(models.TextChoices):
    ONCE = "once", _("One-time")
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")
    INSTALLMENTS = "installments", _("Installments")


# --- Categories --------------------------------------------------------------
#
# Each user has their own categories (stored in the Category table), so they can
# rename, recolour, add and delete them in Settings. New users start with the
# defaults below. The defaults keep a `system_key`, which the statement import
# uses to guess categories ("NETFLIX" -> the "subscriptions" category).


class DefaultExpenseCategory(models.TextChoices):
    HOUSING = "housing", _("Housing & bills")
    FOOD = "food", _("Food & groceries")
    TRANSPORT = "transport", _("Transport")
    SUBSCRIPTIONS = "subscriptions", _("Subscriptions")
    SHOPPING = "shopping", _("Shopping")
    HEALTH = "health", _("Health")
    ENTERTAINMENT = "entertainment", _("Entertainment")
    FEES = "fees", _("Fees & taxes")
    OTHER = "other", _("Other")


class DefaultIncomeCategory(models.TextChoices):
    SALARY = "salary", _("Salary")
    FREELANCE = "freelance", _("Freelance")
    INVESTMENTS = "investments", _("Investments")
    GIFTS = "gifts", _("Gifts")
    REFUNDS = "refunds", _("Refunds")
    OTHER = "other", _("Other")


class Kind(models.TextChoices):
    EXPENSE = "expense", _("Expenses")
    INCOME = "income", _("Income")


# Colours the user can pick for a category (they work in light and dark mode).
PALETTE = [
    "#6c8ebf", "#e0a458", "#6fb39b", "#a08bd6", "#e07a7a", "#5aaec4",
    "#d48bb0", "#b59a6d", "#5cae7e", "#c9755b", "#7f8fd6", "#9a9ea6",
]

DEFAULT_CATEGORIES = {
    # kind: [(system_key, colour), ...] in display order; "other" is always last.
    Kind.EXPENSE: [
        ("housing", "#6c8ebf"), ("food", "#e0a458"), ("transport", "#6fb39b"),
        ("subscriptions", "#a08bd6"), ("shopping", "#e07a7a"), ("health", "#5aaec4"),
        ("entertainment", "#d48bb0"), ("fees", "#b59a6d"), ("other", "#9a9ea6"),
    ],
    Kind.INCOME: [
        ("salary", "#5cae7e"), ("freelance", "#6c8ebf"), ("investments", "#a08bd6"),
        ("gifts", "#d48bb0"), ("refunds", "#5aaec4"), ("other", "#9a9ea6"),
    ],
}
DEFAULT_LABELS = {
    Kind.EXPENSE: dict(DefaultExpenseCategory.choices),
    Kind.INCOME: dict(DefaultIncomeCategory.choices),
}
OTHER = "other"


class Category(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Empty for default categories the user hasn't renamed: they show the translated
    # default name instead, so they follow the user's language.
    name = models.CharField(_("name"), max_length=40, blank=True)
    color = models.CharField(_("colour"), max_length=7, default=PALETTE[0])
    system_key = models.CharField(max_length=20, blank=True, editable=False)
    position = models.PositiveSmallIntegerField(default=500, editable=False)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = _("category")
        verbose_name_plural = _("categories")

    def __str__(self):
        return str(self.label)

    @property
    def label_default(self):
        """The translated name of a default category ("" for categories the user created)."""
        return DEFAULT_LABELS[self.kind].get(self.system_key, "") if self.system_key else ""

    @property
    def label(self):
        return self.name or self.label_default

    @property
    def is_other(self):
        """The "Other" category can't be deleted: deleted categories move their items there."""
        return self.system_key == OTHER


def ensure_default_categories(user):
    """Give a user the default categories, if they don't have any yet."""
    if Category.objects.filter(user=user).exists():
        return
    Category.objects.bulk_create([
        Category(user=user, kind=kind, system_key=key, color=color,
                 position=999 if key == OTHER else index * 10)
        for kind, defaults in DEFAULT_CATEGORIES.items()
        for index, (key, color) in enumerate(defaults)
    ])


def get_category(user, kind, system_key):
    """The user's category for a default key, falling back to "Other"."""
    categories = Category.objects.filter(user=user, kind=kind)
    return (categories.filter(system_key=system_key).first()
            or categories.filter(system_key=OTHER).first())


class EntryQuerySet(models.QuerySet):
    def for_month(self, year, month):
        """Entries that count in the given month.

        - One-time: the month of their date.
        - Monthly: every month from their date onward (until `ends_on`, if set).
        - Yearly: once a year, in the month of their date (until `ends_on`, if set).
        - Installments: every month from the first to the last installment.
        """
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        not_ended = Q(ends_on__isnull=True) | Q(ends_on__gte=first_day)
        return self.filter(
            Q(frequency=Frequency.ONCE, date__year=year, date__month=month)
            | (Q(frequency=Frequency.MONTHLY, date__lte=last_day) & not_ended)
            | (Q(frequency=Frequency.YEARLY, date__lte=last_day, date__month=month) & not_ended)
            | Q(
                frequency=Frequency.INSTALLMENTS,
                first_installment_month__lte=first_day,
                last_installment_month__gte=first_day,
            )
        )


class Entry(models.Model):
    """Fields shared by incomes and expenses."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    description = models.CharField(_("description"), max_length=200)
    amount = models.DecimalField(
        _("amount"), max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    date = models.DateField(_("date"))
    frequency = models.CharField(
        _("repeats"), max_length=12, choices=Frequency.choices, default=Frequency.ONCE
    )
    installment_total = models.PositiveSmallIntegerField(
        _("number of installments"), null=True, blank=True,
        validators=[MinValueValidator(2), MaxValueValidator(120)],
    )
    # The months the installments are paid in (first day of each month).
    first_installment_month = models.DateField(null=True, blank=True, editable=False)
    last_installment_month = models.DateField(null=True, blank=True, editable=False)
    # Monthly/yearly entries can end: e.g. when a subscription's price changes, the old
    # price ends in the previous month and a new entry starts with the new price.
    ends_on = models.DateField(_("last month"), null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = EntryQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.description} ({self.amount})"

    def save(self, *args, **kwargs):
        if self.category_id is None:
            ensure_default_categories(self.user)
            self.category = get_category(self.user, self.kind, self.default_category_key)
        if self.frequency == Frequency.INSTALLMENTS and self.installment_total:
            if self.first_installment_month is None:
                self.first_installment_month = self.date.replace(day=1)
            self.last_installment_month = add_months(self.first_installment_month, self.installment_total - 1)
        else:
            self.installment_total = None
            self.first_installment_month = self.last_installment_month = None
        if self.frequency not in (Frequency.MONTHLY, Frequency.YEARLY):
            self.ends_on = None
        super().save(*args, **kwargs)

    @property
    def is_recurring(self):
        return self.frequency != Frequency.ONCE

    def date_in_month(self, year, month):
        """The day this entry falls on in the given month (for display)."""
        if not self.is_recurring:
            return self.date
        day = min(self.date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def installment_in_month(self, year, month):
        """Which installment (1, 2, ...) is paid in the given month, or None."""
        if self.frequency != Frequency.INSTALLMENTS or not self.first_installment_month:
            return None
        number = months_between(self.first_installment_month, date(year, month, 1)) + 1
        return number if 1 <= number <= self.installment_total else None


class Income(Entry):
    kind = "income"
    default_category_key = DefaultIncomeCategory.SALARY

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="incomes", verbose_name=_("category"),
        limit_choices_to={"kind": Kind.INCOME},
    )

    class Meta(Entry.Meta):
        verbose_name = _("income")
        verbose_name_plural = _("incomes")


class Expense(Entry):
    kind = "expense"
    default_category_key = OTHER

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="expenses", verbose_name=_("category"),
        limit_choices_to={"kind": Kind.EXPENSE},
    )
    original_description = models.CharField(
        _("name on the statement"), max_length=200, blank=True,
        help_text=_("The name exactly as it appeared on the credit card statement."),
    )
    source_file = models.CharField(_("statement file"), max_length=255, blank=True)
    imported = models.BooleanField(default=False)

    class Meta(Entry.Meta):
        verbose_name = _("expense")
        verbose_name_plural = _("expenses")


class Profile(models.Model):
    """Per-user settings."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    language = models.CharField(_("language"), max_length=10, choices=settings.LANGUAGES, default=settings.LANGUAGE_CODE)
    currency = models.CharField(_("currency"), max_length=3, choices=currency_choices(), default=settings.DEFAULT_CURRENCY)

    def __str__(self):
        return f"Settings for {self.user}"


def get_profile(user):
    profile, created = Profile.objects.get_or_create(user=user)
    if created:
        ensure_default_categories(user)
    return profile
