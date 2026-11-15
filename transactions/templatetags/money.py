from django import template

from ..currencies import format_money

register = template.Library()


@register.filter
def money(value, currency=None):
    """{{ amount|money:currency }} -> 'R$ 1.234,50'."""
    return format_money(value, currency)
