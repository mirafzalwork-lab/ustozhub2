"""Context processors для добавления глобальных переменных во все шаблоны"""
from .models import Message, Conversation, Notification
from django.conf import settings
from django.db.models import Q, Count
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

# Единый TTL для badge-кэша (30 секунд - баланс между свежестью и нагрузкой)
BADGE_CACHE_TTL = 30


def invalidate_message_cache(user_id):
    """Сбрасывает кэш badge-ей сообщений для пользователя"""
    cache.delete(f'unread_messages_{user_id}')
    cache.delete(f'conversations_count_{user_id}')


def invalidate_notification_cache(user_id):
    """Сбрасывает кэш badge-ей уведомлений для пользователя"""
    cache.delete(f'unread_notifications_{user_id}')


def _get_user_conversations(user):
    """
    Вспомогательная функция для получения переписок пользователя
    """
    try:
        if not hasattr(user, 'user_type'):
            return Conversation.objects.none()

        if user.user_type == 'teacher':
            if not hasattr(user, 'teacher_profile') or not user.teacher_profile:
                return Conversation.objects.none()

            # Q(student=user) — админ-чаты «Поддержка ↔ этот учитель»
            # (учитель в student-слоте), чтобы их непрочитанные попали в бейдж.
            from django.db.models import Q
            return Conversation.objects.filter(
                Q(teacher=user.teacher_profile) | Q(student=user),
                is_active=True
            ).select_related('teacher', 'student')
        else:
            return Conversation.objects.filter(
                student=user,
                is_active=True
            ).select_related('teacher', 'student')

    except AttributeError as e:
        logger.debug(f"AttributeError in _get_user_conversations for user_id={user.pk}: {e}")
        return Conversation.objects.none()
    except Exception as e:
        logger.error(f"Error in _get_user_conversations for user_id={user.pk}: {e}", exc_info=True)
        return Conversation.objects.none()


def unread_messages_count(request):
    """
    Добавляет количество непрочитанных сообщений для текущего пользователя
    """
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}

    try:
        cache_key = f'unread_messages_{request.user.pk}'
        cached_count = cache.get(cache_key)

        if cached_count is not None:
            return {'unread_messages_count': cached_count}

        user = request.user
        conversations = _get_user_conversations(user)
        conversation_ids = list(conversations.values_list('id', flat=True))

        if not conversation_ids:
            cache.set(cache_key, 0, BADGE_CACHE_TTL)
            return {'unread_messages_count': 0}

        unread_count = Message.objects.filter(
            conversation_id__in=conversation_ids,
            is_read=False
        ).exclude(
            sender=user
        ).count()

        cache.set(cache_key, unread_count, BADGE_CACHE_TTL)
        return {'unread_messages_count': unread_count}

    except Exception as e:
        logger.error(f"Error in unread_messages_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'unread_messages_count': 0}


def user_conversations_count(request):
    """
    Добавляет общее количество активных переписок пользователя
    """
    if not request.user.is_authenticated:
        return {'conversations_count': 0}

    try:
        cache_key = f'conversations_count_{request.user.pk}'
        cached_count = cache.get(cache_key)

        if cached_count is not None:
            return {'conversations_count': cached_count}

        conversations = _get_user_conversations(request.user)
        count = conversations.count()

        cache.set(cache_key, count, BADGE_CACHE_TTL)
        return {'conversations_count': count}

    except Exception as e:
        logger.error(f"Error in user_conversations_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'conversations_count': 0}


def unread_notifications_count(request):
    """
    Добавляет количество непрочитанных уведомлений для текущего пользователя
    """
    if not request.user.is_authenticated:
        return {'unread_notifications_count': 0}

    try:
        cache_key = f'unread_notifications_{request.user.pk}'
        cached_count = cache.get(cache_key)

        if cached_count is not None:
            return {'unread_notifications_count': cached_count}

        count = Notification.get_unread_count(request.user)

        cache.set(cache_key, count, BADGE_CACHE_TTL)
        return {'unread_notifications_count': count}

    except Exception as e:
        logger.error(f"Error in unread_notifications_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'unread_notifications_count': 0}


def telegram_links(request):
    """Ссылки на официальный Telegram канал и бот — доступны во всех шаблонах.

    Без обращений к БД: чистые настройки, чтобы не нагружать каждый запрос.
    Используются в футере (канал) и как fallback-ссылка на бота.
    """
    return {
        'TELEGRAM_CHANNEL_URL': getattr(settings, 'TELEGRAM_CHANNEL_URL', ''),
        'TELEGRAM_CHANNEL_USERNAME': getattr(settings, 'TELEGRAM_CHANNEL_USERNAME', ''),
        'TELEGRAM_BOT_URL': getattr(settings, 'TELEGRAM_BOT_URL', ''),
        'TELEGRAM_BOT_USERNAME': getattr(settings, 'TELEGRAM_BOT_USERNAME', ''),
    }


def telegram_connect(request):
    """Статус привязки Telegram + deep-link для баннера «Подключить».

    Делает баннер `partials/telegram_connect_banner.html` доступным на ЛЮБОЙ
    странице (раньше переменные клались только во вьюхах дашбордов, поэтому на
    уведомлениях и в переписке баннер не показывался). Кэшируется на пользователя
    коротким TTL, чтобы не бить БД проверкой привязки на каждом запросе. Для
    анонимов и staff не считаем — баннер им не нужен.
    """
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated) or getattr(user, 'is_staff', False):
        return {}
    try:
        cache_key = f'tg_connect_ctx_{user.pk}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from telegram_bot.account_link import is_connected, bot_connect_url
        connected = is_connected(user)
        # bot_connect_url генерит одноразовый токен с TTL 1 час (>> TTL кэша),
        # поэтому ссылка останется живой в пределах окна кэширования.
        ctx = {
            'telegram_connected': connected,
            'telegram_connect_url': '' if connected else bot_connect_url(user.pk),
        }
        cache.set(cache_key, ctx, 60)  # 60с — статус привязки меняется редко
        return ctx
    except Exception as e:
        logger.debug(f"telegram_connect context failed for user_id={getattr(user, 'pk', None)}: {e}")
        return {}


def admin_nav_badges(request):
    """Счётчики «требует внимания» для админ-навигации (только для staff).

    Кэшируется на короткое время, чтобы не бить БД на каждом запросе.
    Доступны в шаблонах как admin_badges.{moderation,withdrawals,disputes,requests,total}.
    """
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff):
        return {}
    try:
        cache_key = 'admin_nav_badges'
        cached = cache.get(cache_key)
        if cached is not None:
            return {'admin_badges': cached}

        from .models import Booking, TeacherProfile
        from billing.models import LessonDispute, Subscription, WithdrawalRequest

        badges = {
            'moderation': TeacherProfile.objects.filter(moderation_status='pending').count(),
            'withdrawals': WithdrawalRequest.objects.filter(status='pending').count(),
            'disputes': LessonDispute.objects.filter(status=LessonDispute.Status.OPEN).count(),
            'requests': Subscription.objects.filter(status=Subscription.Status.PENDING_APPROVAL).count(),
        }
        badges['total'] = sum(badges.values())
        # Пробные уроки в ожидании подтверждения учителя — информативный счётчик,
        # НЕ входит в «требует внимания» (подтверждает учитель, не админ).
        badges['trials'] = Booking.objects.filter(is_trial=True, status='pending').count()
        cache.set(cache_key, badges, 30)  # 30s — свежо, но без нагрузки
        return {'admin_badges': badges}
    except Exception as e:
        logger.error(f"Error in admin_nav_badges: {e}", exc_info=True)
        return {}
