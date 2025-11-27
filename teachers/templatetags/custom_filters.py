# logic/templatetags/custom_filters.py
from django import template
from teachers.models import PlatformMessage

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

@register.simple_tag
def get_platform_messages():
    """
    Возвращает активные сообщения платформы для всех пользователей включая гостей
    Использование: {% get_platform_messages as active_messages %}
    """
    return PlatformMessage.objects.filter(is_active=True, show_to_guests=True).order_by('-created_at')[:5]

@register.simple_tag
def get_platform_messages_count():
    """
    Возвращает количество активных сообщений платформы для всех пользователей включая гостей
    Использование: {% get_platform_messages_count as messages_count %}
    """
    return PlatformMessage.objects.filter(is_active=True, show_to_guests=True).count()