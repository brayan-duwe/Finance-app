"""Currencies the user can choose, and how to format amounts in each one."""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.translation import gettext_lazy as _

CURRENCIES = {
    # code: name, symbol, decimal separator, thousands separator, space after symbol,
    #       words that identify this currency in a statement's column header
    "BRL": {"name": _("Brazilian real"), "symbol": "R$", "decimal": ",", "thousands": ".", "space": True,
            "hints": ("r$", "brl", "reais", "real")},
    "USD": {"name": _("US dollar"), "symbol": "$", "decimal": ".", "thousands": ",", "space": False,
            "hints": ("us$", "usd", "u$s", "dollar", "dólar", "dolar")},
    "EUR": {"name": _("Euro"), "symbol": "€", "decimal": ",", "thousands": ".", "space": True,
            "hints": ("€", "eur")},
    "GBP": {"name": _("British pound"), "symbol": "£", "decimal": ".", "thousands": ",", "space": False,
            "hints": ("£", "gbp")},
    "ARS": {"name": _("Argentine peso"), "symbol": "AR$", "decimal": ",", "thousands": ".", "space": True,
            "hints": ("ar$", "ars")},
    "MXN": {"name": _("Mexican peso"), "symbol": "MX$", "decimal": ".", "thousands": ",", "space": False,
            "hints": ("mx$", "mxn")},
}


def currency_choices():
    return [(code, f"{c['symbol']}  {c['name']}") for code, c in CURRENCIES.items()]


def get_currency(code):
    return CURRENCIES.get(code) or CURRENCIES[settings.DEFAULT_CURRENCY]


def format_money(value, code=None):
    """format_money(1234.5, "BRL") -> 'R$ 1.234,50'."""
    try:
        value = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    currency = get_currency(code or settings.DEFAULT_CURRENCY)
    number = f"{abs(value):,.2f}"  # "1,234.50"
    number = number.replace(",", "\0").replace(".", currency["decimal"]).replace("\0", currency["thousands"])
    sign = "-" if value < 0 else ""
    space = " " if currency["space"] else ""
    return f"{sign}{currency['symbol']}{space}{number}"
