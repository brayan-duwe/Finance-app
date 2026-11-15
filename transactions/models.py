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


class Category(models.TextChoices):
    HOUSING = "housing", _("Housing & bills")
    FOOD = "food", _("Food & groceries")
    TRANSPORT = "transport", _("Transport")
    SUBSCRIPTIONS = "subscriptions", _("Subscriptions")
    SHOPPING = "shopping", _("Shopping")
    HEALTH = "health", _("Health")
    ENTERTAINMENT = "entertainment", _("Entertainment")
    FEES = "fees", _("Fees & taxes")
    OTHER = "other", _("Other")


class EntryQuerySet(models.QuerySet):
    def for_month(self, year, month):
        """Entries that count in the given month.

        - One-time: the month of their date.
        - Monthly: every month from their date onward.
        - Yearly: once a year, in the month of their date.
        - Installments: every month from the first to the last installment.
        """
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        return self.filter(
            Q(frequency=Frequency.ONCE, date__year=year, date__month=month)
            | Q(frequency=Frequency.MONTHLY, date__lte=last_day)
            | Q(frequency=Frequency.YEARLY, date__lte=last_day, date__month=month)
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
    created_at = models.DateTimeField(auto_now_add=True)

    objects = EntryQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.description} ({self.amount})"

    def save(self, *args, **kwargs):
        if self.frequency == Frequency.INSTALLMENTS and self.installment_total:
            if self.first_installment_month is None:
                self.first_installment_month = self.date.replace(day=1)
            self.last_installment_month = add_months(self.first_installment_month, self.installment_total - 1)
        else:
            self.installment_total = None
            self.first_installment_month = self.last_installment_month = None
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

    class Meta(Entry.Meta):
        verbose_name = _("income")
        verbose_name_plural = _("incomes")


class Expense(Entry):
    kind = "expense"

    category = models.CharField(_("category"), max_length=20, choices=Category.choices, default=Category.OTHER)
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
    profile, _created = Profile.objects.get_or_create(user=user)
    return profile
