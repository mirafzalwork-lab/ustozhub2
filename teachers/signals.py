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
    Signal: Отправляет уведомление в Telegram когда создается новое сообщение
    
    Args:
        sender: Класс модели (Message)
        instance: Экземпляр сообщения
        created: True если сообщение только что создано
    """
    # Отправляем уведомление только для новых сообщений
    if not created:
        return
    
    try:
        from telegram_bot.notifications import send_telegram_notification
        
        # Получаем данные
        conversation = instance.conversation
        sender_user = instance.sender
        
        # Определяем получателя (тот, кто НЕ отправитель)
        if conversation.teacher.user == sender_user:
            # Отправитель - учитель, получатель - ученик
            recipient = conversation.student
        else:
            # Отправитель - ученик, получатель - учитель
            recipient = conversation.teacher.user
        
        # Отправляем уведомление
        sender_name = sender_user.get_full_name() or sender_user.username
        message_preview = instance.content[:100]  # Первые 100 символов
        
        success = send_telegram_notification(
            user=recipient,
            sender_name=sender_name,
            message_preview=message_preview
        )
        
        if success:
            logger.info(f"Уведомление отправлено пользователю {recipient.username}")
        else:
            logger.warning(f"Не удалось отправить уведомление пользователю {recipient.username}")
            
    except Exception as e:
        # Не прерываем создание сообщения даже если уведомление не отправлено
        logger.error(f"Ошибка отправки уведомления о сообщении: {e}")


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

