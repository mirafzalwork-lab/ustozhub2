"""
Публикация нового преподавателя в публичном Telegram-канале.

Собирает сообщение (фото + подпись + inline-кнопка) и отправляет его в канал
через Telegram Bot API. python-telegram-bot 21 — асинхронный, поэтому наружу
отдаём синхронную обёртку publish_teacher() для вызова из Celery-задачи.
"""
import asyncio
import html
import logging

from django.conf import settings
from django.utils.translation import gettext as _
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger('telegram_bot.channel')

# Лимит подписи к фото в Telegram — 1024 символа. Оставляем запас под разметку.
CAPTION_LIMIT = 1024


def _profile_url(teacher):
    return f"{settings.SITE_URL.rstrip('/')}/teacher/{teacher.id}/"


def build_caption(teacher):
    """Формирует HTML-подпись. Пользовательский ввод экранируется."""
    user = teacher.user
    name = html.escape(user.get_full_name().strip() or user.username)

    subjects = ', '.join(
        ts.subject.name
        for ts in teacher.teachersubject_set.select_related('subject').all()
    )

    lines = [
        _('🎉 Новый преподаватель на платформе!'),
        '',
        f"👨‍🏫 <b>{name}</b>",
    ]
    if subjects:
        lines.append(f"📚 {html.escape(subjects)}")
    if teacher.city:
        lines.append(f"📍 {html.escape(teacher.city.name)}")
    lines.append(_('🕒 Опыт: %(years)d лет') % {'years': teacher.experience_years})

    price = teacher.get_min_price()
    if price:
        price_fmt = f"{int(price):,}".replace(',', ' ')
        lines.append(_('💰 от %(price)s сум/урок') % {'price': price_fmt})

    certs = teacher.certificates.count()
    if certs:
        lines.append(_('🎓 Сертификатов: %(n)d') % {'n': certs})

    if teacher.bio:
        bio = teacher.bio.strip()
        # Урезаем описание так, чтобы весь текст уложился в лимит подписи.
        head = '\n'.join(lines) + '\n\n«»'
        room = CAPTION_LIMIT - len(head)
        if room > 0:
            if len(bio) > room:
                bio = bio[:max(0, room - 1)].rstrip() + '…'
            lines.append('')
            lines.append(f"«{html.escape(bio)}»")

    return '\n'.join(lines)


def build_keyboard(teacher):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(_('👉 Записаться на урок'), url=_profile_url(teacher))
    ]])


def _read_avatar_bytes(teacher):
    """Синхронно читает файл аватара в память (ORM/IO вне async-контекста)."""
    avatar = getattr(teacher.user, 'avatar', None)
    if not (avatar and getattr(avatar, 'name', '')):
        return None
    try:
        with avatar.open('rb') as fh:
            return fh.read()
    except (FileNotFoundError, OSError) as e:
        logger.warning("Аватар учителя %s недоступен (%s), шлём без фото",
                       teacher.id, e)
        return None


async def _send_async(caption, keyboard, photo_bytes):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    chat_id = settings.TELEGRAM_CHANNEL_ID
    async with bot:
        if photo_bytes:
            msg = await bot.send_photo(
                chat_id=chat_id, photo=photo_bytes, caption=caption,
                parse_mode='HTML', reply_markup=keyboard,
            )
        else:
            # Нет фото — текстовый пост с превью ссылки профиля.
            msg = await bot.send_message(
                chat_id=chat_id, text=caption, parse_mode='HTML',
                reply_markup=keyboard, disable_web_page_preview=False,
            )
        return msg.message_id


def publish_teacher(teacher):
    """Синхронная обёртка. Возвращает message_id или бросает исключение.

    Все обращения к ORM/файлам делаем здесь (sync), в async уходят только
    готовые примитивы — иначе Django блокирует ORM в event loop.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError('TELEGRAM_BOT_TOKEN не задан')
    if not getattr(settings, 'TELEGRAM_CHANNEL_ID', ''):
        raise RuntimeError('TELEGRAM_CHANNEL_ID не задан')

    caption = build_caption(teacher)
    keyboard = build_keyboard(teacher)
    photo_bytes = _read_avatar_bytes(teacher)
    return asyncio.run(_send_async(caption, keyboard, photo_bytes))
