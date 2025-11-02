"""
Context processors для добавления глобальных переменных во все шаблоны
"""
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
