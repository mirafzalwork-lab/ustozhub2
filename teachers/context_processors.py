"""Context processors для добавления глобальных переменных во все шаблоны"""
from .models import Message, Conversation, Notification
from django.db.models import Q, Count
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


def _get_user_conversations(user):
    """
    ✅ Вспомогательная функция для получения переписок пользователя
    Избегает дублирования кода
    """
    try:
        if not hasattr(user, 'user_type'):
            return Conversation.objects.none()
        
        if user.user_type == 'teacher':
            # Для учителя - проверяем наличие профиля
            if not hasattr(user, 'teacher_profile') or not user.teacher_profile:
                return Conversation.objects.none()
            
            return Conversation.objects.filter(
                teacher=user.teacher_profile,
                is_active=True
            ).select_related('teacher', 'student')
        else:
            # Для ученика - переписки где он является студентом
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
    ✅ Добавляет количество непрочитанных сообщений для текущего пользователя
    во все шаблоны с кэшированием
    """
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}
    
    try:
        # ✅ Проверяем кэш
        cache_key = f'unread_messages_{request.user.pk}'
        cached_count = cache.get(cache_key)
        
        if cached_count is not None:
            logger.debug(f"Cache hit for unread_messages_count: user_id={request.user.pk}")
            return {'unread_messages_count': cached_count}
        
        user = request.user
        conversations = _get_user_conversations(user)
        conversation_ids = list(conversations.values_list('id', flat=True))
        
        if not conversation_ids:
            cache.set(cache_key, 0, 300)  # ✅ Кэшируем на 5 минут
            return {'unread_messages_count': 0}
        
        # ✅ Используем одиночный запрос со счетом
        unread_count = Message.objects.filter(
            conversation_id__in=conversation_ids,
            is_read=False
        ).exclude(
            sender=user
        ).count()
        
        # ✅ Кэшируем результат
        cache.set(cache_key, unread_count, 300)
        
        logger.debug(f"Calculated unread_messages_count: user_id={request.user.pk}, count={unread_count}")
        return {'unread_messages_count': unread_count}
        
    except Exception as e:
        logger.error(f"Error in unread_messages_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'unread_messages_count': 0}


def user_conversations_count(request):
    """
    ✅ Добавляет общее количество активных переписок пользователя
    с кэшированием
    """
    if not request.user.is_authenticated:
        return {'conversations_count': 0}
    
    try:
        # ✅ Проверяем кэш
        cache_key = f'conversations_count_{request.user.pk}'
        cached_count = cache.get(cache_key)
        
        if cached_count is not None:
            logger.debug(f"Cache hit for conversations_count: user_id={request.user.pk}")
            return {'conversations_count': cached_count}
        
        conversations = _get_user_conversations(request.user)
        count = conversations.count()
        
        # ✅ Кэшируем результат
        cache.set(cache_key, count, 300)
        
        logger.debug(f"Calculated conversations_count: user_id={request.user.pk}, count={count}")
        return {'conversations_count': count}
        
    except Exception as e:
        logger.error(f"Error in user_conversations_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'conversations_count': 0}


def unread_notifications_count(request):
    """
    ✅ Добавляет количество непрочитанных уведомлений для текущего пользователя
    во все шаблоны с кэшированием
    """
    if not request.user.is_authenticated:
        return {'unread_notifications_count': 0}
    
    try:
        # ✅ Проверяем кэш
        cache_key = f'unread_notifications_{request.user.pk}'
        cached_count = cache.get(cache_key)
        
        if cached_count is not None:
            logger.debug(f"Cache hit for unread_notifications_count: user_id={request.user.pk}")
            return {'unread_notifications_count': cached_count}
        
        # ✅ Используем метод модели если доступен, иначе директный запрос
        try:
            count = Notification.get_unread_count(request.user)
        except (AttributeError, TypeError):
            # Fallback если метода нет
            count = Notification.objects.filter(
                recipient=request.user,
                is_read=False
            ).count()
        
        # ✅ Кэшируем результат
        cache.set(cache_key, count, 300)
        
        logger.debug(f"Calculated unread_notifications_count: user_id={request.user.pk}, count={count}")
        return {'unread_notifications_count': count}
    
    except Exception as e:
        logger.error(f"Error in unread_notifications_count for user_id={request.user.pk}: {e}", exc_info=True)
        return {'unread_notifications_count': 0}
