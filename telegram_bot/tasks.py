"""
Celery задачи для обработки очереди уведомлений
"""

import logging
from celery import shared_task
from django.utils import timezone
from django.db import models, transaction  # ✅ ИСПРАВЛЕНО: Добавлен недостающий импорт
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
        
        # Используем select_for_update для предотвращения race conditions
        with transaction.atomic():
            failed_notifications = NotificationQueue.objects.select_for_update(
                skip_locked=True
            ).filter(
                status='failed',
                retry_count__lt=models.F('max_retries')
            )
            
            # Оптимизация: собираем id для bulk update
            to_retry_ids = []
            for notification in failed_notifications:
                if notification.can_retry():
                    to_retry_ids.append(notification.id)
            
            # Bulk update для производительности
            if to_retry_ids:
                NotificationQueue.objects.filter(
                    id__in=to_retry_ids
                ).update(
                    status='pending',
                    scheduled_at=now
                )
                
                retry_count = len(to_retry_ids)
                logger.info(f"Celery task: Запланировано повторно {retry_count} уведомлений")
                return retry_count
        
        return 0
        
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
        
        # Батчевое удаление для больших датасетов (по 1000 записей)
        total_deleted = 0
        batch_size = 1000
        
        while True:
            with transaction.atomic():
                ids_to_delete = list(
                    NotificationLog.objects.filter(
                        timestamp__lt=cutoff_date
                    ).values_list('id', flat=True)[:batch_size]
                )
                
                if not ids_to_delete:
                    break
                
                deleted_count, _ = NotificationLog.objects.filter(
                    id__in=ids_to_delete
                ).delete()
                
                total_deleted += deleted_count
                
                if deleted_count < batch_size:
                    break
        
        if total_deleted > 0:
            logger.info(f"Celery task: Удалено {total_deleted} старых логов уведомлений")
        
        return total_deleted
        
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
        
        # Батчевое удаление для больших датасетов (по 1000 записей)
        total_deleted = 0
        batch_size = 1000
        
        while True:
            with transaction.atomic():
                ids_to_delete = list(
                    NotificationQueue.objects.filter(
                        status__in=['sent', 'cancelled'],
                        sent_at__lt=cutoff_date
                    ).values_list('id', flat=True)[:batch_size]
                )
                
                if not ids_to_delete:
                    break
                
                deleted_count, _ = NotificationQueue.objects.filter(
                    id__in=ids_to_delete
                ).delete()
                
                total_deleted += deleted_count
                
                if deleted_count < batch_size:
                    break
        
        if total_deleted > 0:
            logger.info(f"Celery task: Удалено {total_deleted} старых уведомлений")
        
        return total_deleted
        
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
        
        with transaction.atomic():
            # Используем select_for_update для предотвращения race conditions
            stuck_notifications = NotificationQueue.objects.select_for_update(
                skip_locked=True
            ).filter(
                status='processing',
                processing_started_at__lt=cutoff_time
            )
            
            # Разделяем на две группы для bulk update
            to_retry_ids = []
            to_fail_ids = []
            
            for notification in stuck_notifications:
                if notification.can_retry():
                    to_retry_ids.append(notification.id)
                else:
                    to_fail_ids.append(notification.id)
            
            # Bulk updates
            if to_retry_ids:
                NotificationQueue.objects.filter(
                    id__in=to_retry_ids
                ).update(
                    status='pending',
                    scheduled_at=timezone.now()
                )
            
            if to_fail_ids:
                NotificationQueue.objects.filter(
                    id__in=to_fail_ids
                ).update(
                    status='failed',
                    last_error=f"Timeout после {timeout_minutes} минут обработки"
                )
            
            cancelled_count = len(to_retry_ids) + len(to_fail_ids)
            
            if cancelled_count > 0:
                logger.warning(f"Celery task: Отменено {cancelled_count} зависших уведомлений (retry: {len(to_retry_ids)}, failed: {len(to_fail_ids)})")
            
            return cancelled_count
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task cancel_stuck_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='health_check_notifications')
def health_check_notifications():
    """
    Проверка здоровья системы уведомлений
    Запускается каждые 5 минут для мониторинга
    
    Использование в settings.py:
    CELERY_BEAT_SCHEDULE = {
        'health-check-notifications': {
            'task': 'health_check_notifications',
            'schedule': 300.0,  # каждые 5 минут
        },
    }
    """
    try:
        from teachers.models import NotificationQueue
        
        stats = {
            'pending': NotificationQueue.objects.filter(status='pending').count(),
            'processing': NotificationQueue.objects.filter(status='processing').count(),
            'failed': NotificationQueue.objects.filter(status='failed').count(),
            'sent_last_hour': NotificationQueue.objects.filter(
                status='sent',
                sent_at__gte=timezone.now() - timedelta(hours=1)
            ).count()
        }
        
        # Логируем предупреждение если очередь слишком большая
        if stats['pending'] > 100:
            logger.warning(f"⚠️ Большая очередь уведомлений: {stats['pending']} pending")
        
        if stats['processing'] > 10:
            logger.warning(f"⚠️ Много уведомлений в обработке: {stats['processing']} processing")
        
        if stats['failed'] > 50:
            logger.warning(f"⚠️ Много неудачных уведомлений: {stats['failed']} failed")
        
        logger.info(f"📊 Health check: pending={stats['pending']}, processing={stats['processing']}, failed={stats['failed']}, sent_last_hour={stats['sent_last_hour']}")
        
        return stats
        
    except Exception as e:
        logger.error(f"Ошибка в Celery task health_check_notifications: {e}", exc_info=True)
        return {}
