"""
Django signals для автоматической отправки уведомлений
"""

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import (
    Message, Subject, City, TeacherSubject, StudentProfile,
    SubjectCategory, TeacherProfile, Review, ProfileView,
    Notification
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
        
        # ✅ Проверяем что conversation существует
        if not conversation:
            logger.error(f"Сообщение {message.pk or 'new'} не связано с диалогом")
            return
        
        # Безопасно получаем sender_user
        if not sender_user:
            logger.error(f"Сообщение {message.pk or 'new'} не имеет отправителя")
            return
        
        # Проверяем доступ к related objects
        try:
            # ✅ Используем .pk для безопасной проверки без дополнительного запроса
            if not conversation.teacher or not conversation.teacher.user:
                logger.error(f"Диалог {conversation.pk} не имеет учителя или профиля учителя")
                return
            
            teacher_user = conversation.teacher.user
            student_user = conversation.student
            
            if not student_user:
                logger.error(f"Диалог {conversation.pk} не имеет ученика")
                return
                
        except AttributeError as e:
            logger.error(
                f"Ошибка доступа к пользователям диалога {conversation.pk}: {e}",
                exc_info=True
            )
            return
        
        # Получатель - это не отправитель
        if sender_user.pk == teacher_user.pk:
            recipient = student_user
        elif sender_user.pk == student_user.pk:
            recipient = teacher_user
        else:
            logger.warning(
                f"Отправитель {sender_user.pk} ({sender_user.username}) "
                f"не является участником диалога {conversation.pk}"
            )
            return

        # Проверяем что получатель существует
        if not recipient:
            logger.error(f"Не удалось определить получателя для сообщения {message.pk or 'new'}")
            return

        # Сбрасываем кэш badge и пушим real-time уведомление получателю
        try:
            from .context_processors import invalidate_message_cache
            invalidate_message_cache(recipient.pk)
            from .consumers import notify_user
            sender_display = sender_user.get_full_name() or sender_user.username
            notify_user(recipient.pk, 'new_message', {
                'sender_name': sender_display,
                'preview': (message.content[:80] if message.content else ''),
                'conversation_id': str(conversation.pk),
            })
        except Exception as e:
            logger.debug(f"Push notification failed: {e}")

        # Добавляем уведомление в очередь
        sender_name = sender_user.get_full_name() or sender_user.username
        # ⚡ ОПТИМИЗАЦИЯ: Ограничиваем длину preview
        message_preview = (message.content[:100] if message.content else "[файл]")
        
        notification = queue_new_message_notification(
            recipient=recipient,
            sender_name=sender_name,
            message_preview=message_preview,
            conversation_id=str(conversation.pk)  # UUID -> str для JSON
        )
        
        if notification:
            logger.info(
                f"✅ Уведомление о сообщении добавлено в очередь для {recipient.username}"
            )
        else:
            logger.debug(
                f"❌ Уведомление не добавлено - пользователь {recipient.username} "
                f"не подключил Telegram или отключил уведомления"
            )
            
    except Exception as e:
        # Не прерываем создание сообщения даже если уведомление не добавлено
        logger.error(
            f"Ошибка добавления уведомления о сообщении: {e}",
            exc_info=True
        )


# =============================================================================
# ⚡ ОПТИМИЗАЦИЯ: Автоматический сброс кэша при изменении данных
# =============================================================================

@receiver([post_save, post_delete], sender=Subject)
def clear_subjects_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш предметов при их изменении"""
    try:
        cache.delete('all_subjects')
        
        # ✅ Очищаем кэш категории если предмет связан с категорией
        if instance and instance.category_id:
            cache.delete(f'category_subjects_count_{instance.category_id}')
            logger.debug(f"Кэш категории {instance.category_id} очищен")
        
        logger.debug("Кэш предметов очищен")
    except Exception as e:
        logger.error(f"Ошибка очистки кэша предметов: {e}", exc_info=True)


@receiver([post_save, post_delete], sender=SubjectCategory)
def clear_category_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш категории при её изменении"""
    try:
        if instance and instance.pk:
            cache.delete(f'category_subjects_count_{instance.pk}')
            logger.debug(f"Кэш категории {instance.pk} очищен")
        
        # Очищаем общий кэш предметов тоже
        cache.delete('all_subjects')
    except Exception as e:
        logger.error(f"Ошибка очистки кэша категории: {e}", exc_info=True)


@receiver([post_save, post_delete], sender=City)
def clear_cities_cache(sender, **kwargs):
    """Сбрасывает кэш городов при их изменении"""
    try:
        cache.delete('all_cities')
        logger.debug("Кэш городов очищен")
    except Exception as e:
        logger.error(f"Ошибка очистки кэша городов: {e}", exc_info=True)


@receiver([post_save, post_delete], sender=TeacherSubject)
def clear_price_range_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш диапазона цен при изменении предметов учителей"""
    try:
        cache.delete('price_range')
        
        # ✅ Очищаем кэш конкретного учителя и предмета
        if instance:
            if instance.teacher_id:
                cache.delete(f'teacher_min_price_{instance.teacher_id}')
                logger.debug(f"Кэш цен учителя {instance.teacher_id} очищен")
            
            if instance.subject_id:
                cache.delete(f'subject_teachers_count_{instance.subject_id}')
                logger.debug(f"Кэш учителей предмета {instance.subject_id} очищен")
        
        logger.debug("Кэш диапазона цен очищен")
    except Exception as e:
        logger.error(f"Ошибка очистки кэша цен: {e}", exc_info=True)


@receiver([post_save, post_delete], sender=StudentProfile)
def clear_budget_range_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш диапазона бюджета при изменении профилей учеников"""
    try:
        cache.delete('budget_range')
        
        # ✅ Очищаем кэш статистики конкретного ученика
        if instance and instance.pk and hasattr(instance, 'clear_cache'):
            try:
                instance.clear_cache()
                logger.debug(f"Кэш профиля ученика {instance.pk} очищен")
            except Exception as e:
                logger.warning(f"Не удалось очистить кэш ученика {instance.pk}: {e}")
        
        logger.debug("Кэш диапазона бюджета очищен")
    except Exception as e:
        logger.error(f"Ошибка очистки кэша бюджета: {e}", exc_info=True)


# =============================================================================
# 🔧 ДОПОЛНИТЕЛЬНЫЕ SIGNALS ДЛЯ ПОЛНОЙ ИНВАЛИДАЦИИ КЭША
# =============================================================================

@receiver([post_save, post_delete], sender=TeacherProfile)
def clear_teacher_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш учителя при изменении его профиля"""
    if instance and instance.pk and hasattr(instance, 'clear_cache'):
        try:
            instance.clear_cache()
            logger.debug(f"Кэш профиля учителя {instance.pk} очищен")
        except Exception as e:
            logger.warning(f"Не удалось очистить кэш учителя {instance.pk}: {e}")


@receiver([post_save, post_delete], sender=Review)
def clear_teacher_reviews_cache(sender, instance=None, **kwargs):
    """Сбрасывает кэш учителя при добавлении/удалении отзыва"""
    try:
        if instance and instance.teacher_id:
            # Очищаем кэш рейтинга и отзывов учителя
            cache.delete(f'teacher_reviews_{instance.teacher_id}')
            cache.delete(f'teacher_rating_{instance.teacher_id}')
            logger.debug(f"Кэш отзывов учителя {instance.teacher_id} очищен")

            # Пересчитываем ранжирование учителя после нового отзыва
            try:
                teacher = instance.teacher
                if teacher and teacher.is_active:
                    teacher.update_ranking_score()
                    logger.debug(f"Ранжирование учителя {teacher.pk} обновлено")
            except Exception as e:
                logger.warning(f"Не удалось обновить ранжирование: {e}")
    except Exception as e:
        logger.error(f"Ошибка очистки кэша отзывов: {e}", exc_info=True)


@receiver(post_save, sender=ProfileView)
def update_view_stats_cache(sender, instance=None, created=False, **kwargs):
    """
    Обновляет кэш статистики просмотров
    Инвалидация происходит автоматически в ProfileView.save()
    """
    if created and instance:
        try:
            if instance.profile_type == 'teacher' and instance.teacher_profile:
                logger.debug(f"Зарегистрирован просмотр профиля учителя {instance.teacher_profile.id}")
            elif instance.profile_type == 'student' and instance.student_profile:
                logger.debug(f"Зарегистрирован просмотр профиля ученика {instance.student_profile.id}")
        except Exception:
            pass


# =============================================================================
# REAL-TIME PUSH: уведомления через WebSocket при создании Notification
# =============================================================================

@receiver(post_save, sender=Notification)
def push_notification_realtime(sender, instance, created, **kwargs):
    """
    При создании нового Notification — пушим real-time событие целевым пользователям.
    """
    if not created or not instance.is_active:
        return

    try:
        from .consumers import notify_user
        from .context_processors import invalidate_notification_cache
        from .models import User

        payload = {
            'id': instance.id,
            'title': instance.title,
            'short_text': instance.short_text,
        }

        if instance.target == 'specific_user' and instance.target_user_id:
            # Персональное уведомление — пушим одному пользователю
            invalidate_notification_cache(instance.target_user_id)
            notify_user(instance.target_user_id, 'new_notification', payload)

        elif instance.target == 'all':
            # Всем пользователям — пушим каждому активному
            for uid in User.objects.filter(is_active=True).values_list('id', flat=True):
                invalidate_notification_cache(uid)
                notify_user(uid, 'new_notification', payload)

        elif instance.target in ('students', 'teachers'):
            user_type = 'student' if instance.target == 'students' else 'teacher'
            for uid in User.objects.filter(
                is_active=True, user_type=user_type
            ).values_list('id', flat=True):
                invalidate_notification_cache(uid)
                notify_user(uid, 'new_notification', payload)

        elif instance.target == 'admins':
            for uid in User.objects.filter(
                is_active=True, is_staff=True
            ).values_list('id', flat=True):
                invalidate_notification_cache(uid)
                notify_user(uid, 'new_notification', payload)

    except Exception as e:
        logger.error(f"Error pushing real-time notification id={instance.pk}: {e}", exc_info=True)

