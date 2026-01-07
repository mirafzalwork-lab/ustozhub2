"""
Context processors для добавления глобальных переменных во все шаблоны
"""
from .models import Message, Conversation, Notification
from django.db.models import Q


def unread_messages_count(request):
    """
    Добавляет количество непрочитанных сообщений для текущего пользователя
    во все шаблоны
    """
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}
    
    try:
        user = request.user
        
        # Проверяем тип пользователя и получаем его переписки
        if hasattr(user, 'user_type') and user.user_type == 'teacher':
            # Для учителя - проверяем наличие профиля
            if not hasattr(user, 'teacher_profile'):
                return {'unread_messages_count': 0}
            
            # Переписки где пользователь является учителем
            conversations = Conversation.objects.filter(
                teacher=user.teacher_profile,
                is_active=True
            ).select_related('teacher', 'student')
            
        else:
            # Для ученика - переписки где он является студентом
            conversations = Conversation.objects.filter(
                student=user,
                is_active=True
            ).select_related('teacher', 'student')
        
        # Получаем ID всех активных переписок
        conversation_ids = conversations.values_list('id', flat=True)
        
        if not conversation_ids:
            return {'unread_messages_count': 0}
        
        # Считаем непрочитанные сообщения (не от текущего пользователя)
        unread_count = Message.objects.filter(
            conversation_id__in=conversation_ids,
            is_read=False
        ).exclude(
            sender=user
        ).count()
        
        return {'unread_messages_count': unread_count}
        
    except AttributeError as e:
        # Ошибка доступа к атрибутам (например, teacher_profile не существует)
        print(f"AttributeError in unread_messages_count: {e}")
        return {'unread_messages_count': 0}
    except Exception as e:
        # Любая другая ошибка
        print(f"Error in unread_messages_count context processor: {e}")
        return {'unread_messages_count': 0}


def user_conversations_count(request):
    """
    Добавляет общее количество активных переписок пользователя
    """
    if not request.user.is_authenticated:
        return {'conversations_count': 0}
    
    try:
        user = request.user
        
        if hasattr(user, 'user_type') and user.user_type == 'teacher':
            if not hasattr(user, 'teacher_profile'):
                return {'conversations_count': 0}
            
            count = Conversation.objects.filter(
                teacher=user.teacher_profile,
                is_active=True
            ).count()
        else:
            count = Conversation.objects.filter(
                student=user,
                is_active=True
            ).count()
        
        return {'conversations_count': count}
        
    except Exception as e:
        print(f"Error in user_conversations_count: {e}")
        return {'conversations_count': 0}


def unread_notifications_count(request):
    """
    Добавляет количество непрочитанных уведомлений для текущего пользователя
    во все шаблоны
    """
    if not request.user.is_authenticated:
        return {'unread_notifications_count': 0}
    
    try:
        count = Notification.get_unread_count(request.user)
        return {'unread_notifications_count': count}
    except Exception as e:
        print(f"Error in unread_notifications_count: {e}")
        return {'unread_notifications_count': 0}
