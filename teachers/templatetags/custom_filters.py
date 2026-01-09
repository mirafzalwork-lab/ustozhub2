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

@register.filter
def attr(obj, attr_name):
    """
    Get attribute from object by name
    Usage: {{ form|attr:"field_name" }}
    """
    return getattr(obj, attr_name, None)

@register.filter
def add(value, arg):
    """
    Add the arg to the value
    Usage: {{ "field_"|add:i }}
    """
    try:
        return str(value) + str(arg)
    except (ValueError, TypeError):
        return ''
