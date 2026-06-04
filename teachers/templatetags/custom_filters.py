# logic/templatetags/custom_filters.py
import re

from django import template

register = template.Library()

# Диапазоны эмодзи/пиктограмм Unicode. Используется для очистки текстов
# уведомлений от стикеров — вместо них в UI рисуются Font Awesome иконки.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F\U00002190-\U000021FF\U00002700-\U000027BF]+",
    flags=re.UNICODE,
)


@register.filter
def strip_emoji(value):
    """Убирает эмодзи/стикеры из строки и схлопывает лишние пробелы.

    Нужно для legacy-уведомлений, у которых эмодзи зашит прямо в заголовок.
    """
    if not value:
        return value
    cleaned = _EMOJI_RE.sub('', str(value))
    return re.sub(r'\s{2,}', ' ', cleaned).strip()

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


@register.simple_tag
def star_rating(rating, size_class=''):
    """
    Рендерит 5 звёзд (full / half / empty) с округлением до ближайшей
    половины — чтобы 4.5 показывало 4 полных + половину, а не 4 как раньше.
    Использование: {% star_rating teacher.rating "rating-star-large" %}
    """
    from django.utils.safestring import mark_safe
    try:
        r = float(rating or 0)
    except (TypeError, ValueError):
        r = 0.0
    # округление до ближайшей 0.5
    nearest_half = round(r * 2) / 2
    parts = []
    for i in range(1, 6):
        if nearest_half >= i:
            cls = 'fas fa-star'
        elif nearest_half >= i - 0.5:
            cls = 'fas fa-star-half-alt'
        else:
            cls = 'far fa-star'
        parts.append(f'<i class="{cls} {size_class}" aria-hidden="true"></i>')
    return mark_safe(''.join(parts))


@register.filter
def mask_secret(value):
    """Маскирует платёжные реквизиты, показывая только последние 4 символа.

    '8600123412341234' -> '•••• 1234'. Значения короче 5 символов скрываются
    полностью. Используется в истории выводов, чтобы не светить номер карты.
    Usage: {{ r.payout_details|mask_secret }}
    """
    s = str(value or '').strip()
    alnum = ''.join(ch for ch in s if ch.isalnum())
    if len(alnum) <= 4:
        return '••••'
    return '•••• ' + alnum[-4:]
