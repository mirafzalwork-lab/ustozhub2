"""Привязка Telegram-аккаунта к пользователю сайта через deep-link токен.

Поток:
1. Web (баннер «Подключить бота») вызывает ``bot_connect_url(user_id)`` —
   генерируется одноразовый токен, кладётся в общий кэш (Redis шарится между
   gunicorn и процессом бота), возвращается ссылка ``t.me/<bot>?start=<token>``.
2. Пользователь жмёт «Старт» в боте → ``/start <token>``.
3. Бот вызывает ``consume_connect_token(token)`` → получает user_id и
   привязывает ``TelegramUser.user``.

Токен — короткий (укладывается в лимит Telegram start-payload 64 символа,
charset a-z0-9), одноразовый, живёт 1 час.
"""
from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import get_random_string

CONNECT_TTL = 3600  # 1 час
CONNECT_PREFIX = 'tglink:'
_ALLOWED = 'abcdefghijklmnopqrstuvwxyz0123456789'


def make_connect_token(user_id):
    """Генерирует одноразовый токен привязки и сохраняет его в кэше."""
    token = get_random_string(32, allowed_chars=_ALLOWED)
    cache.set(f'{CONNECT_PREFIX}{token}', int(user_id), CONNECT_TTL)
    return token


def consume_connect_token(token):
    """Возвращает user_id по токену и сразу удаляет его (one-time). None если нет."""
    if not token:
        return None
    key = f'{CONNECT_PREFIX}{token}'
    user_id = cache.get(key)
    if user_id is not None:
        cache.delete(key)
    return user_id


def bot_connect_url(user_id):
    """Deep-link на бота с токеном привязки для конкретного пользователя сайта."""
    username = (getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or '').lstrip('@')
    token = make_connect_token(user_id)
    return f'https://t.me/{username}?start={token}'


def link_telegram_to_user(telegram_user, user_id):
    """Привязывает TelegramUser к пользователю сайта.

    Снимает возможную предыдущую привязку этого пользователя к другому
    Telegram-аккаунту (OneToOne), затем назначает нового. Идемпотентно.
    """
    from teachers.models import TelegramUser

    if not user_id:
        return False
    # Снять старую привязку этого user к другому telegram-аккаунту.
    TelegramUser.objects.filter(user_id=user_id).exclude(
        pk=telegram_user.pk
    ).update(user=None)
    if telegram_user.user_id != int(user_id):
        telegram_user.user_id = int(user_id)
        telegram_user.save(update_fields=['user'])
    return True


def is_connected(user):
    """True если у пользователя сайта есть привязанный и активный Telegram-бот."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from teachers.models import TelegramUser
    return TelegramUser.objects.filter(user=user, started_bot=True).exists()
