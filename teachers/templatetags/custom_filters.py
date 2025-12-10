# logic/templatetags/custom_filters.py
from django import template

register = template.Library()

@register.filter
def split(value, delimiter=','):
    """
    Разделяет строку по разделителю
    Использование: {{ some_string|split:"," }}
    """
    if value:
        return value.split(delimiter)
    return []