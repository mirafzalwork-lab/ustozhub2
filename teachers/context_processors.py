"""
Context processors для добавления глобальных переменных во все шаблоны
"""
from django.db import models
from .models import Message, Conversation


def unread_messages_count(request):
    """
    Добавляет количество непрочитанных сообщений для текущего пользователя
    во все шаблоны
    """
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}
    
    try:
        user = request.user
        
        # Получаем все переписки пользователя
        if user.user_type == 'teacher':
            # Для учителя - переписки где он является teacher
            conversations = Conversation.objects.filter(
                teacher=user.teacher_profile,
                is_active=True
            )
        else:
            # Для ученика - переписки где он является student
            conversations = Conversation.objects.filter(
                student=user,
                is_active=True
            )
        
        # Считаем непрочитанные сообщения (не от текущего пользователя)
        unread_count = Message.objects.filter(
            conversation__in=conversations,
            is_read=False
        ).exclude(
            sender=user
        ).count()
        
        return {'unread_messages_count': unread_count}
        
    except Exception as e:
        # В случае ошибки возвращаем 0
        print(f"Error in unread_messages_count context processor: {e}")
        return {'unread_messages_count': 0}


def platform_messages(request):
    """
    Добавляет активные сообщения платформы для текущего пользователя
    Работает как для зарегистрированных, так и для гостей
    """
    try:
        from .models import PlatformMessage, UserMessageRead
        from django.utils import timezone
        
        user = request.user if request.user.is_authenticated else None
        
        # Получаем активные сообщения, которые должны показываться
        active_messages = PlatformMessage.objects.filter(
            is_active=True
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )
        
        # Фильтруем сообщения по пользователю
        user_messages = []
        for message in active_messages:
            if message.should_show_to_user(user):
                user_messages.append(message)
        
        # Для незарегистрированных пользователей
        if not user or not user.is_authenticated:
            return {
                'platform_messages': user_messages,
                'unread_platform_messages': user_messages,  # Все сообщения "непрочитанные" для гостей
                'unread_platform_messages_count': len(user_messages)
            }
        
        # Для зарегистрированных пользователей - проверяем прочитанные
        read_message_ids = UserMessageRead.objects.filter(
            user=user,
            message__in=user_messages
        ).values_list('message_id', flat=True)
        
        # Разделяем на прочитанные и непрочитанные
        unread_messages = [msg for msg in user_messages if msg.id not in read_message_ids]
        
        return {
            'platform_messages': user_messages,
            'unread_platform_messages': unread_messages,
            'unread_platform_messages_count': len(unread_messages),
            'read_message_ids': list(read_message_ids)
        }
        
    except Exception as e:
        print(f"Error in platform_messages context processor: {e}")
        return {
            'platform_messages': [], 
            'unread_platform_messages': [],
            'unread_platform_messages_count': 0,
            'read_message_ids': []
        }
