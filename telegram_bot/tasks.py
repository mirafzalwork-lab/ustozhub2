"""
Celery задачи для обработки очереди уведомлений
"""

import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(name='process_notification_queue')
def process_notification_queue():
    """
    Обрабатывает очередь уведомлений
    Запускается каждые 10 секунд через Celery Beat
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'process-notification-queue': {
            'task': 'process_notification_queue',
            'schedule': 10.0,  # каждые 10 секунд
        },
    }
    """
    try:
        from telegram_bot.notification_service import process_notification_queue as process_queue
        
        sent_count = process_queue(batch_size=10)
        
        if sent_count > 0:
            logger.info(f"Celery task: Обработано уведомлений: {sent_count}")
        
        return sent_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task process_notification_queue: {e}", exc_info=True)
        return 0


@shared_task(name='retry_failed_notifications')
def retry_failed_notifications():
    """
    Повторная попытка для failed уведомлений, которые можно повторить
    Запускается каждый час
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'retry-failed-notifications': {
            'task': 'retry_failed_notifications',
            'schedule': crontab(minute=0),  # каждый час
        },
    }
    """
    try:
        from teachers.models import NotificationQueue
        
        now = timezone.now()
        
        # Находим failed уведомления, которые можно повторить
        failed_notifications = NotificationQueue.objects.filter(
            status='failed',
            retry_count__lt=models.F('max_retries')
        )
        
        retry_count = 0
        for notification in failed_notifications:
            if notification.can_retry():
                # Сбрасываем статус на pending
                notification.status = 'pending'
                notification.scheduled_at = now
                notification.save()
                retry_count += 1
        
        if retry_count > 0:
            logger.info(f"Celery task: Запланировано повторно {retry_count} уведомлений")
        
        return retry_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task retry_failed_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='cleanup_old_notification_logs')
def cleanup_old_notification_logs(days=30):
    """
    Очистка старых логов уведомлений
    Запускается раз в день
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'cleanup-notification-logs': {
            'task': 'cleanup_old_notification_logs',
            'schedule': crontab(hour=3, minute=0),  # в 3:00 каждый день
        },
    }
    """
    try:
        from teachers.models import NotificationLog
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        deleted_count, _ = NotificationLog.objects.filter(
            timestamp__lt=cutoff_date
        ).delete()
        
        if deleted_count > 0:
            logger.info(f"Celery task: Удалено {deleted_count} старых логов уведомлений")
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task cleanup_old_notification_logs: {e}", exc_info=True)
        return 0


@shared_task(name='cleanup_old_notifications')
def cleanup_old_notifications(days=90):
    """
    Очистка старых обработанных уведомлений
    Запускается раз в день
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'cleanup-old-notifications': {
            'task': 'cleanup_old_notifications',
            'schedule': crontab(hour=3, minute=30),  # в 3:30 каждый день
        },
    }
    """
    try:
        from teachers.models import NotificationQueue
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # Удаляем только sent и cancelled уведомления
        deleted_count, _ = NotificationQueue.objects.filter(
            status__in=['sent', 'cancelled'],
            sent_at__lt=cutoff_date
        ).delete()
        
        if deleted_count > 0:
            logger.info(f"Celery task: Удалено {deleted_count} старых уведомлений")
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task cleanup_old_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='cancel_stuck_notifications')
def cancel_stuck_notifications(timeout_minutes=30):
    """
    Отменяет "зависшие" уведомления в статусе processing
    Запускается каждые 15 минут
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'cancel-stuck-notifications': {
            'task': 'cancel_stuck_notifications',
            'schedule': 900.0,  # каждые 15 минут
        },
    }
    """
    try:
        from teachers.models import NotificationQueue
        
        cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)
        
        # Находим уведомления в processing, которые начали обрабатываться давно
        stuck_notifications = NotificationQueue.objects.filter(
            status='processing',
            processing_started_at__lt=cutoff_time
        )
        
        cancelled_count = 0
        for notification in stuck_notifications:
            if notification.can_retry():
                # Возвращаем в pending для повторной попытки
                notification.status = 'pending'
                notification.scheduled_at = timezone.now()
                notification.save()
            else:
                # Отменяем если превышен лимит попыток
                notification.status = 'failed'
                notification.last_error = f"Timeout после {timeout_minutes} минут обработки"
                notification.save()
            cancelled_count += 1
        
        if cancelled_count > 0:
            logger.warning(f"Celery task: Отменено {cancelled_count} зависших уведомлений")
        
        return cancelled_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task cancel_stuck_notifications: {e}", exc_info=True)
        return 0
