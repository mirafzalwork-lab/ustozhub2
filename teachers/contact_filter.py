"""Анти-обход платформы (v2 Шаг 7): маскировка контактов в чате.

Цель — не дать ученику и учителю «увести» сделку мимо платформы через обмен
телефонами/мессенджерами/ссылками в личных сообщениях до того, как платформа
заработала на связке. После порога доверия (CONTACT_MASK_MIN_PAID_LESSONS
оплаченных уроков) обмен контактами разрешается.

Маскируем server-side во всех путях отправки (AJAX-форма и WebSocket).
"""
from __future__ import annotations

import re

from django.conf import settings

# Плейсхолдер вместо вырезанного контакта.
_MASK = '••• [контакт скрыт — общайтесь через платформу]'

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

# Замаскированные «словесные» цифры мы не трогаем (слишком много ложных
# срабатываний) — это сознательный компромисс MVP.


def mask_contacts(text: str) -> tuple[str, bool]:
    """Заменяет телефоны/ссылки/@хендлы на плейсхолдер.

    Возвращает (очищенный_текст, было_ли_замаскировано).
    """
    if not text:
        return text, False
    masked = text
    masked = _URL_RE.sub(_MASK, masked)
    masked = _PHONE_RE.sub(_MASK, masked)
    masked = _HANDLE_RE.sub(_MASK, masked)
    return masked, (masked != text)


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


def apply_contact_policy(conversation, text: str) -> tuple[str, bool]:
    """Применяет политику маскировки к сообщению диалога.

    conversation: teachers.models.Conversation (есть .teacher и .student).
    Возвращает (текст_для_сохранения, было_ли_замаскировано).
    """
    try:
        if not should_mask_for_pair(conversation.student, conversation.teacher):
            return text, False
    except Exception:
        # При любой ошибке вычисления порога — безопаснее замаскировать.
        pass
    return mask_contacts(text)
