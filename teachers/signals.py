"""
Django signals для автоматической отправки уведомлений
"""

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import (
    Message, Subject, City, TeacherSubject, StudentProfile, 
    SubjectCategory, TeacherProfile, Review, ProfileView
)
import logging

logger = logging.getLogger(__name__)

# ⚡ ОПТИМИЗАЦИЯ: Импортируем функцию один раз на уровне модуля
try:
    from telegram_bot.notification_service import queue_new_message_notification
    TELEGRAM_BOT_AVAILABLE = True
except ImportError:
    TELEGRAM_BOT_AVAILABLE = False
    logger.warning("⚠️ Telegram bot не доступен - уведомления отключены")


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
    if not created or not TELEGRAM_BOT_AVAILABLE:
        return
    
    try:
        # Получаем данные
        message = instance
        conversation = message.conversation
        sender_user = message.sender
        
        # Проверяем доступ к related objects
        try:
            teacher_user = conversation.teacher.user
            student_user = conversation.student
        except AttributeError as e:
            logger.error(f"Ошибка доступа к пользователям диалога {conversation.id}: {e}")
            return
        
        # Получатель - это не отправитель
        if sender_user.id == teacher_user.id:
            recipient = student_user
        elif sender_user.id == student_user.id:
            recipient = teacher_user
        else:
            logger.warning(
                f"Отправитель {sender_user.id} ({sender_user.username}) "
                f"не является участником диалога {conversation.id}"
            )
            return
        
        # Проверяем что получатель существует
        if not recipient:
            logger.error(f"Не удалось определить получателя для сообщения {message.id}")
            return
        
        # Добавляем уведомление в очередь
        sender_name = sender_user.get_full_name() or sender_user.username
        # ⚡ ОПТИМИЗАЦИЯ: Ограничиваем длину preview
        message_preview = (message.content[:100] if message.content else "[файл]")
        
        notification = queue_new_message_notification(
            recipient=recipient,
            sender_name=sender_name,
            message_preview=message_preview,
            conversation_id=str(conversation.id)  # UUID -> str для JSON
        )
        
        if notification:
            logger.info(
                f"✅ Уведомление о сообщении {message.id} добавлено в очередь "
                f"для {recipient.username}"
            )
        else:
            logger.warning(
                f"❌ Уведомление не добавлено - пользователь {recipient.username} "
                f"не подключил Telegram или отключил уведомления"
            )
            
    except Exception as e:
        # Не прерываем создание сообщения даже если уведомление не добавлено
        logger.error(
            f"Ошибка добавления уведомления о сообщении {instance.id}: {e}",
            exc_info=True
        )


# =============================================================================
# ⚡ ОПТИМИЗАЦИЯ: Автоматический сброс кэша при изменении данных
# =============================================================================

@receiver([post_save, post_delete], sender=Subject)
def clear_subjects_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш предметов при их изменении"""
    cache.delete('all_subjects')
    
    # ✅ Очищаем кэш категории если предмет связан с категорией
    if instance and hasattr(instance, 'category') and instance.category:
        cache.delete(f'category_subjects_count_{instance.category.id}')
        logger.info(f"Кэш категории {instance.category.id} очищен")
    
    logger.info("Кэш предметов очищен")


@receiver([post_save, post_delete], sender=SubjectCategory)
def clear_category_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш категории при её изменении"""
    if instance:
        cache.delete(f'category_subjects_count_{instance.id}')
        logger.info(f"Кэш категории {instance.id} очищен")
    
    # Очищаем общий кэш предметов тоже
    cache.delete('all_subjects')


@receiver([post_save, post_delete], sender=City)
def clear_cities_cache(sender, **kwargs):
    """Сбрасывает кэш городов при их изменении"""
    cache.delete('all_cities')
    logger.info("Кэш городов очищен")


@receiver([post_save, post_delete], sender=TeacherSubject)
def clear_price_range_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш диапазона цен при изменении предметов учителей"""
    cache.delete('price_range')
    
    # ✅ Очищаем кэш конкретного учителя и предмета
    if instance:
        if hasattr(instance, 'teacher') and instance.teacher:
            cache.delete(f'teacher_min_price_{instance.teacher.id}')
            logger.info(f"Кэш цен учителя {instance.teacher.id} очищен")
        
        if hasattr(instance, 'subject') and instance.subject:
            cache.delete(f'subject_teachers_count_{instance.subject.id}')
            logger.info(f"Кэш учителей предмета {instance.subject.id} очищен")
    
    logger.info("Кэш диапазона цен очищен")


@receiver([post_save, post_delete], sender=StudentProfile)
def clear_budget_range_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш диапазона бюджета при изменении профилей учеников"""
    cache.delete('budget_range')
    
    # ✅ Очищаем кэш статистики конкретного ученика
    if instance and hasattr(instance, 'clear_cache'):
        try:
            instance.clear_cache()
            logger.info(f"Кэш профиля ученика {instance.id} очищен")
        except Exception as e:
            logger.warning(f"Не удалось очистить кэш ученика {instance.id}: {e}")
    
    logger.info("Кэш диапазона бюджета очищен")


# =============================================================================
# 🔧 ДОПОЛНИТЕЛЬНЫЕ SIGNALS ДЛЯ ПОЛНОЙ ИНВАЛИДАЦИИ КЭША
# =============================================================================

@receiver([post_save, post_delete], sender=TeacherProfile)
def clear_teacher_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш учителя при изменении его профиля"""
    if instance and hasattr(instance, 'clear_cache'):
        try:
            instance.clear_cache()
            logger.info(f"Кэш профиля учителя {instance.id} очищен")
        except Exception as e:
            logger.warning(f"Не удалось очистить кэш учителя {instance.id}: {e}")


@receiver([post_save, post_delete], sender=Review)
def clear_teacher_reviews_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш учителя при добавлении/удалении отзыва"""
    if instance and hasattr(instance, 'teacher'):
        # Очищаем кэш рейтинга и отзывов учителя
        cache.delete(f'teacher_reviews_{instance.teacher.id}')
        cache.delete(f'teacher_rating_{instance.teacher.id}')
        logger.info(f"Кэш отзывов учителя {instance.teacher.id} очищен")


@receiver(post_save, sender=ProfileView)
def update_view_stats_cache(sender, instance=None, created=False, **kwargs):
    """
    Обновляет кэш статистики просмотров
    Инвалидация происходит автоматически в ProfileView.save()
    """
    if created and instance:
        # Дополнительное логирование если нужно
        try:
            if instance.profile_type == 'teacher' and instance.teacher_profile:
                logger.debug(f"Зарегистрирован просмотр профиля учителя {instance.teacher_profile.id}")
            elif instance.profile_type == 'student' and instance.student_profile:
                logger.debug(f"Зарегистрирован просмотр профиля ученика {instance.student_profile.id}")
        except Exception:
            pass

