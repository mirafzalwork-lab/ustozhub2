from django import template

register = template.Library()

@register.filter(name='get_field')
def get_field(form, field_name):
    """
    Template filter to get a form field by name dynamically.
    Usage: {{ form|get_field:"field_name" }}
    """
    try:
        return form[field_name]
    except (KeyError, TypeError):
        return ''

@register.simple_tag
def get_form_field(form, prefix, number):
    """
    Template tag to get a form field with prefix and number.
    Usage: {% get_form_field form "subject_" i %}
    """
    field_name = f"{prefix}{number}"
    try:
        return form[field_name]
    except KeyError:
        return ''