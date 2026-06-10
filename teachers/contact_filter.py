"""Анти-обход платформы (v2 Шаг 7): маскировка контактов в чате.

Цель — не дать ученику и учителю «увести» сделку мимо платформы через обмен
телефонами/мессенджерами/ссылками в личных сообщениях до того, как платформа
заработала на связке. После порога доверия (CONTACT_MASK_MIN_PAID_LESSONS
оплаченных уроков) обмен контактами разрешается.

Маскируем server-side во всех путях отправки (AJAX-форма и WebSocket).
"""
from __future__ import annotations

import re
import unicodedata

from django.conf import settings

# Плейсхолдер вместо вырезанного контакта.
_MASK = '••• [контакт скрыт — общайтесь через платформу]'

# Символы нулевой ширины / вариации / keycap — ими разбивают цифры и точки,
# чтобы обойти регэкспы (9​9​8…, 9️⃣9️⃣8️⃣, t‍.me). Удаляем перед матчингом.
#   200b zwsp · 200c zwnj · 200d zwj · 2060 word-joiner · feff bom
#   fe0f variation-selector-16 · 20e3 combining-enclosing-keycap
_ZERO_WIDTH_TRANS = dict.fromkeys(
    map(ord, '​‌‍⁠﻿️⃣'), None
)


def _normalize(text: str) -> str:
    """NFKC + удаление zero-width/keycap.

    NFKC схлопывает полноширинные цифры (９→9), точку-лидер (t․me→t.me),
    полноширинный ＠→@. Затем убираем невидимые разделители — после этого
    обычные регэкспы ловят разбитые цифры/эмодзи-цифры. Нормализация обычного
    кириллического/латинского текста практически идемпотентна.
    """
    return unicodedata.normalize('NFKC', text).translate(_ZERO_WIDTH_TRANS)

# --- Регэкспы потенциальных контактов -------------------------------------

# Телефоны: последовательность из 7+ цифр, допускающая +, пробелы, -, (), точки.
# Узбекский формат +998 90 123 45 67 и любые длинные цифровые цепочки.
_PHONE_RE = re.compile(
    r'(?<!\w)'
    r'(?:\+?\d[\s\-().]?){7,}\d'
)

# Ссылки (http/https и «голые» домены t.me/wa.me/instagram и т.п.).
_URL_RE = re.compile(
    r'\b(?:https?://|www\.)\S+'
    r'|\b(?:t\.me|telegram\.me|wa\.me|whatsapp\.com|instagram\.com|'
    r'facebook\.com|vk\.com|youtube\.com|youtu\.be)/\S*',
    re.IGNORECASE,
)

# @упоминания (telegram-хендлы): @ + 4+ буквенно-цифровых/нижнее подчёркивание.
_HANDLE_RE = re.compile(r'(?<!\w)@[A-Za-z0-9_]{4,}')

# Хендл БЕЗ @ после ключевого слова мессенджера: «telegram ivan_teacher»,
# «тг: @user» (уже ловится выше), «вотсап +998…» (телефон ловится отдельно).
# Ловим «<ключевое_слово> <разделители> <username>».
_KEYWORD_HANDLE_RE = re.compile(
    r'(?:telegram|telega|телеграм|телега|тelegram|инста|instagram|whatsapp|'
    r'вотсап|ватсап|вацап|тг|tg)\b'
    r'[\s:,\-—=]*'
    r'@?[A-Za-z][A-Za-z0-9_]{3,}',
    re.IGNORECASE,
)

# «Голые» домены вне http (any word.tld/...) — ловим распространённые TLD,
# чтобы поймать gmail.com/discord.gg/signal.me и т.п. вне белого списка _URL_RE.
_BARE_DOMAIN_RE = re.compile(
    r'\b[A-Za-z0-9](?:[A-Za-z0-9\-]{0,40})'
    r'\.(?:com|net|org|ru|uz|me|gg|io|app|link|site|online|club|info|biz|'
    r'dev|xyz|tg|su|pro|store|shop|tech|space|click|top|live|chat|fun)'
    r'(?:/\S*)?\b',
    re.IGNORECASE,
)

# --- Числительные прописью (обход «девять девять восемь…») -----------------
# Словарь однозначных числительных ru/uz/en. Детект СОЗНАТЕЛЬНО консервативный:
# срабатывает только при ≥7 числительных И наличии «контактного» намерения
# (звони/номер/телефон/raqam/call/телеграм). Иначе легитимный урок счёта
# («один два три …» у репетитора-языковеда) не маскируется — на этой платформе
# обучение числам нормально. Чистые числительные без ключевого слова остаются
# дырой — осознанный компромисс ради нулевых ложных срабатываний.
_NUMERAL_WORD_RE = re.compile(
    r'\b(?:'
    r'ноль|один|два|три|четыре|пять|шесть|семь|восемь|девять|'
    r'nol|bir|ikki|uch|tort|besh|olti|yetti|sakkiz|toqqiz|'
    r'zero|one|two|three|four|five|six|seven|eight|nine'
    r')\b',
    re.IGNORECASE,
)
_CONTACT_INTENT_RE = re.compile(
    r'(?:звони|позвони|номер|телефон|\bтел\b|raqam|qongiroq|\bcall\b|'
    r'whatsapp|вотсап|ватсап|телеграм|телега|\bтг\b)',
    re.IGNORECASE,
)
_NUMERAL_PHONE_MIN_WORDS = 7


def _looks_like_spelled_phone(text: str) -> bool:
    """Похоже ли на телефон, записанный числительными прописью."""
    if not _CONTACT_INTENT_RE.search(text):
        return False
    return len(_NUMERAL_WORD_RE.findall(text)) >= _NUMERAL_PHONE_MIN_WORDS


def mask_contacts(text: str) -> tuple[str, bool]:
    """Заменяет телефоны/ссылки/@хендлы на плейсхолдер.

    Возвращает (очищенный_текст, было_ли_замаскировано). Матчинг идёт по
    нормализованному тексту (NFKC + удаление невидимых разделителей), чтобы
    ловить обходы полноширинными/эмодзи-цифрами и zero-width. Если ничего не
    нашли — возвращаем ИСХОДНЫЙ текст без изменений (нормализацию не навязываем).
    """
    if not text:
        return text, False
    normalized = _normalize(text)
    # Телефон числительными прописью с контактным намерением → маскируем целиком.
    if _looks_like_spelled_phone(normalized):
        return _MASK, True
    masked = normalized
    masked = _URL_RE.sub(_MASK, masked)
    masked = _KEYWORD_HANDLE_RE.sub(_MASK, masked)
    masked = _BARE_DOMAIN_RE.sub(_MASK, masked)
    masked = _PHONE_RE.sub(_MASK, masked)
    masked = _HANDLE_RE.sub(_MASK, masked)
    if masked != normalized:
        return masked, True
    # Контактов нет — отдаём оригинал нетронутым (не навязываем нормализацию).
    return text, False


def paid_lessons_between(student, teacher_profile) -> int:
    """Сколько уроков уже «доставлено» в паре (ученик, учитель).

    Доставленный = completed или no_show_student (платформа получила комиссию).
    """
    from .models import Booking
    return Booking.objects.filter(
        student=student,
        slot__teacher=teacher_profile,
        status__in=('completed', 'no_show_student'),
    ).count()


def should_mask_for_pair(student, teacher_profile) -> bool:
    """Маскируем, пока в паре меньше порога оплаченных уроков."""
    threshold = getattr(settings, 'CONTACT_MASK_MIN_PAID_LESSONS', 5)
    if threshold <= 0:
        return False
    return paid_lessons_between(student, teacher_profile) < threshold


def mask_for_pair(student, teacher_profile, text: str) -> tuple[str, bool]:
    """Маскировка контактов для произвольной пары (без объекта Conversation).

    Используется вне чата — первое сообщение брони, ответ учителя, отзывы.
    Возвращает (текст_для_сохранения, было_ли_замаскировано).
    """
    if not text:
        return text, False
    try:
        if not should_mask_for_pair(student, teacher_profile):
            return text, False
    except Exception:
        # При любой ошибке вычисления порога — безопаснее замаскировать.
        pass
    return mask_contacts(text)


def apply_contact_policy(conversation, text: str) -> tuple[str, bool]:
    """Применяет политику маскировки к сообщению диалога.

    conversation: teachers.models.Conversation (есть .teacher и .student).
    Возвращает (текст_для_сохранения, было_ли_замаскировано).
    """
    return mask_for_pair(conversation.student, conversation.teacher, text)
