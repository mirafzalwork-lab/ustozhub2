"""
Django signals для автоматической отправки уведомлений
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Message
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Message)
def send_message_notification(sender, instance, created, **kwargs):
    """
    Signal: Добавляет уведомление в очередь когда создается новое сообщение
    Использует новую систему с retry и rate limiting
    
    Args:
        sender: Класс модели (Message)
        instance: Экземпляр сообщения
        created: True если сообщение только что создано
    """
    # Отправляем уведомление только для новых сообщений
    if not created:
        return
    
    try:
        from telegram_bot.notification_service import queue_new_message_notification
        
        # Получаем данные
        message = instance
        conversation = message.conversation
        sender_user = message.sender
        
        # Определяем получателя (тот, кто НЕ отправитель)
        participants = conversation.participants.all()
        
        if len(participants) != 2:
            logger.warning(f"Диалог {conversation.id} имеет {len(participants)} участников (ожидалось 2)")
            return
        
        # Получатель - это не отправитель
        recipient = None
        for participant in participants:
            if participant != sender_user:
                recipient = participant
                break
        
        if not recipient:
            logger.error(f"Не удалось определить получателя для сообщения {message.id}")
            return
        
        # Не отправляем уведомление самому себе
        if recipient == sender_user:
            return
        
        # Добавляем уведомление в очередь
        sender_name = sender_user.get_full_name() or sender_user.username
        message_preview = message.text if message.text else "[файл]"
        
        notification = queue_new_message_notification(
            recipient=recipient,
            sender_name=sender_name,
            message_preview=message_preview,
            conversation_id=conversation.id
        )
        
        if notification:
            logger.info(f"✅ Уведомление о сообщении {message.id} добавлено в очередь для {recipient.username}")
        else:
            logger.warning(f"❌ Уведомление не добавлено - пользователь {recipient.username} не подключил Telegram или отключил уведомления")
            
    except Exception as e:
        # Не прерываем создание сообщения даже если уведомление не добавлено
        logger.error(f"Ошибка добавления уведомления о сообщении: {e}", exc_info=True)


# =============================================================================
# ⚡ ОПТИМИЗАЦИЯ: Автоматический сброс кэша при изменении данных
# =============================================================================

from django.db.models.signals import post_delete
from django.core.cache import cache
from .models import Subject, City, TeacherSubject, StudentProfile


@receiver([post_save, post_delete], sender=Subject)
def clear_subjects_cache(sender, **kwargs):
    """Сбрасывает кэш предметов при их изменении"""
    cache.delete('all_subjects')
    logger.info("Кэш предметов очищен")


@receiver([post_save, post_delete], sender=City)
def clear_cities_cache(sender, **kwargs):
    """Сбрасывает кэш городов при их изменении"""
    cache.delete('all_cities')
    logger.info("Кэш городов очищен")


@receiver([post_save, post_delete], sender=TeacherSubject)
def clear_price_range_cache(sender, **kwargs):
    """Сбрасывает кэш диапазона цен при изменении предметов учителей"""
    cache.delete('price_range')
    logger.info("Кэш диапазона цен очищен")


@receiver([post_save, post_delete], sender=StudentProfile)
def clear_budget_range_cache(sender, **kwargs):
    """Сбрасывает кэш диапазона бюджета при изменении профилей учеников"""
    cache.delete('budget_range')
    logger.info("Кэш диапазона бюджета очищен")

