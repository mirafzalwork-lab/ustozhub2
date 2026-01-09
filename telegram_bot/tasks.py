"""
Celery задачи для обработки очереди уведомлений
"""

import logging
from celery import shared_task
from django.utils import timezone
from django.db import models, transaction  # ✅ ИСПРАВЛЕНО: Добавлен недостающий импорт
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(name='process_notification_queue', bind=True)
def process_notification_queue(self, batch_size=10):
    """
    ✅ Обрабатывает очередь уведомлений
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
        # ✅ Валидация параметра batch_size
        if not isinstance(batch_size, int) or batch_size <= 0:
            logger.error(f"Invalid batch_size: {batch_size}")
            return 0
        
        if batch_size > 100:  # Ограничиваем максимальный batch size
            logger.warning(f"batch_size {batch_size} is too large, using 100")
            batch_size = 100
        
        # ✅ Безопасный импорт с обработкой ошибок
        try:
            from telegram_bot.notification_service import process_notification_queue as process_queue
        except ImportError as e:
            logger.error(f"Failed to import notification_service: {e}")
            return 0
        
        logger.debug(f"Starting notification queue processing with batch_size={batch_size}")
        
        sent_count = process_queue(batch_size=batch_size)
        
        if sent_count > 0:
            logger.info(f"✅ Processed {sent_count} notifications")
        else:
            logger.debug("No notifications to process")
        
        return sent_count
        
    except Exception as e:
        logger.error(f"Error in process_notification_queue: {e}", exc_info=True)
        # Возвращаем 0 вместо исключения, чтобы не прерывать Celery Beat
        return 0


@shared_task(name='retry_failed_notifications', bind=True, time_limit=600)
def retry_failed_notifications(self):
    """
    ✅ Повторная попытка для failed уведомлений, которые можно повторить
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
        
        logger.debug("Starting retry_failed_notifications task")
        now = timezone.now()
        
        # ✅ Используем select_for_update для предотвращения race conditions
        with transaction.atomic():
            failed_notifications = NotificationQueue.objects.select_for_update(
                skip_locked=True
            ).filter(
                status='failed',
                retry_count__lt=models.F('max_retries')
            ).select_related('recipient')  # ✅ Оптимизация запроса
            
            # Оптимизация: собираем id для bulk update
            to_retry_ids = []
            for notification in failed_notifications[:1000]:  # ✅ Ограничиваем количество обработаний за раз
                try:
                    if notification.can_retry():
                        to_retry_ids.append(notification.id)
                except Exception as e:
                    logger.error(f"Error checking retry status for notification {notification.id}: {e}")
                    continue
            
            # Bulk update для производительности
            if to_retry_ids:
                try:
                    updated_count = NotificationQueue.objects.filter(
                        id__in=to_retry_ids
                    ).update(
                        status='pending',
                        scheduled_at=now,
                        retry_count=models.F('retry_count') + 1  # ✅ Инкрементируем счетчик
                    )
                    
                    logger.info(f"✅ Queued {updated_count} notifications for retry")
                    return updated_count
                except Exception as e:
                    logger.error(f"Error updating notifications: {e}", exc_info=True)
        
        logger.debug("No failed notifications to retry")
        return 0
        
    except ImportError as e:
        logger.error(f"Failed to import NotificationQueue: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error in retry_failed_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='cleanup_old_notification_logs', bind=True, time_limit=1800)
def cleanup_old_notification_logs(self, days=30):
    """
    ✅ Очистка старых логов уведомлений
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
        # ✅ Валидация параметра days
        if not isinstance(days, int) or days <= 0:
            logger.error(f"Invalid days parameter: {days}")
            return 0
        
        if days > 365:
            logger.warning(f"days={days} is too large, using 365")
            days = 365
        
        from teachers.models import NotificationLog
        
        logger.debug(f"Starting cleanup of notification logs older than {days} days")
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # ✅ Батчевое удаление для больших датасетов
        total_deleted = 0
        batch_size = 1000
        batch_count = 0
        
        while True:
            try:
                with transaction.atomic():
                    ids_to_delete = list(
                        NotificationLog.objects.filter(
                            timestamp__lt=cutoff_date
                        ).values_list('id', flat=True)[:batch_size]
                    )
                    
                    if not ids_to_delete:
                        logger.debug(f"No more logs to delete")
                        break
                    
                    deleted_count, _ = NotificationLog.objects.filter(
                        id__in=ids_to_delete
                    ).delete()
                    
                    total_deleted += deleted_count
                    batch_count += 1
                    
                    logger.debug(f"Deleted batch {batch_count}: {deleted_count} logs")
                    
                    if deleted_count < batch_size:
                        break
            
            except Exception as e:
                logger.error(f"Error deleting batch {batch_count}: {e}")
                # Продолжаем попытку следующего батча
                continue
        
        if total_deleted > 0:
            logger.info(f"✅ Cleaned up {total_deleted} old notification logs ({batch_count} batches)")
        else:
            logger.debug("No old logs to clean up")
        
        return total_deleted
        
    except ImportError as e:
        logger.error(f"Failed to import NotificationLog: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error in cleanup_old_notification_logs: {e}", exc_info=True)
        return 0


@shared_task(name='cleanup_old_notifications', bind=True, time_limit=1800)
def cleanup_old_notifications(self, days=90):
    """
    ✅ Очистка старых обработанных уведомлений
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
        # ✅ Валидация параметра days
        if not isinstance(days, int) or days <= 0:
            logger.error(f"Invalid days parameter: {days}")
            return 0
        
        if days > 365:
            logger.warning(f"days={days} is too large, using 365")
            days = 365
        
        from teachers.models import NotificationQueue
        
        logger.debug(f"Starting cleanup of notifications older than {days} days")
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # ✅ Батчевое удаление для больших датасетов
        total_deleted = 0
        batch_size = 1000
        batch_count = 0
        
        while True:
            try:
                with transaction.atomic():
                    ids_to_delete = list(
                        NotificationQueue.objects.filter(
                            status__in=['sent', 'cancelled'],
                            sent_at__lt=cutoff_date
                        ).values_list('id', flat=True)[:batch_size]
                    )
                    
                    if not ids_to_delete:
                        logger.debug("No more notifications to delete")
                        break
                    
                    deleted_count, _ = NotificationQueue.objects.filter(
                        id__in=ids_to_delete
                    ).delete()
                    
                    total_deleted += deleted_count
                    batch_count += 1
                    
                    logger.debug(f"Deleted batch {batch_count}: {deleted_count} notifications")
                    
                    if deleted_count < batch_size:
                        break
            
            except Exception as e:
                logger.error(f"Error deleting batch {batch_count}: {e}")
                continue
        
        if total_deleted > 0:
            logger.info(f"✅ Cleaned up {total_deleted} old notifications ({batch_count} batches)")
        else:
            logger.debug("No old notifications to clean up")
        
        return total_deleted
        
    except ImportError as e:
        logger.error(f"Failed to import NotificationQueue: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error in cleanup_old_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='cancel_stuck_notifications', bind=True, time_limit=300)
def cancel_stuck_notifications(self, timeout_minutes=30):
    """
    ✅ Отменяет "зависшие" уведомления в статусе processing
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
        # ✅ Валидация параметра timeout_minutes
        if not isinstance(timeout_minutes, int) or timeout_minutes <= 0:
            logger.error(f"Invalid timeout_minutes parameter: {timeout_minutes}")
            return 0
        
        if timeout_minutes > 1440:  # Больше чем сутки
            logger.warning(f"timeout_minutes={timeout_minutes} is too large, using 1440")
            timeout_minutes = 1440
        
        from teachers.models import NotificationQueue
        
        logger.debug(f"Starting cancel_stuck_notifications with timeout={timeout_minutes} minutes")
        
        cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)
        
        with transaction.atomic():
            # ✅ Используем select_for_update для предотвращения race conditions
            stuck_notifications = NotificationQueue.objects.select_for_update(
                skip_locked=True
            ).filter(
                status='processing',
                processing_started_at__lt=cutoff_time
            ).select_related('recipient')  # ✅ Оптимизация
            
            # Разделяем на две группы для bulk update
            to_retry_ids = []
            to_fail_ids = []
            
            for notification in stuck_notifications[:1000]:  # ✅ Ограничиваем количество
                try:
                    if notification.can_retry():
                        to_retry_ids.append(notification.id)
                    else:
                        to_fail_ids.append(notification.id)
                except Exception as e:
                    logger.error(f"Error checking notification {notification.id}: {e}")
                    to_fail_ids.append(notification.id)  # В случае ошибки помечаем как failed
            
            # ✅ Bulk updates с обработкой исключений
            try:
                if to_retry_ids:
                    retry_updated = NotificationQueue.objects.filter(
                        id__in=to_retry_ids
                    ).update(
                        status='pending',
                        scheduled_at=timezone.now(),
                        retry_count=models.F('retry_count') + 1
                    )
                    logger.debug(f"Marked {retry_updated} notifications for retry")
            except Exception as e:
                logger.error(f"Error updating retry notifications: {e}")
            
            try:
                if to_fail_ids:
                    fail_updated = NotificationQueue.objects.filter(
                        id__in=to_fail_ids
                    ).update(
                        status='failed',
                        last_error=f"Timeout после {timeout_minutes} минут обработки"
                    )
                    logger.debug(f"Marked {fail_updated} notifications as failed")
            except Exception as e:
                logger.error(f"Error updating failed notifications: {e}")
            
            cancelled_count = len(to_retry_ids) + len(to_fail_ids)
            
            if cancelled_count > 0:
                logger.warning(
                    f"⚠️ Cancelled {cancelled_count} stuck notifications "
                    f"(retry: {len(to_retry_ids)}, failed: {len(to_fail_ids)})"
                )
            else:
                logger.debug("No stuck notifications found")
            
            return cancelled_count
        
    except ImportError as e:
        logger.error(f"Failed to import NotificationQueue: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error in cancel_stuck_notifications: {e}", exc_info=True)
        return 0


@shared_task(name='health_check_notifications', bind=True, time_limit=60)
def health_check_notifications(self):
    """
    ✅ Проверка здоровья системы уведомлений
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
        
        logger.debug("Starting health_check_notifications")
        
        # ✅ Используем select_for_update с skip_locked для избежания блокировок
        try:
            pending_count = NotificationQueue.objects.filter(status='pending').count()
            processing_count = NotificationQueue.objects.filter(status='processing').count()
            failed_count = NotificationQueue.objects.filter(status='failed').count()
            sent_count = NotificationQueue.objects.filter(
                status='sent',
                sent_at__gte=timezone.now() - timedelta(hours=1)
            ).count()
        except Exception as e:
            logger.error(f"Error fetching notification stats: {e}")
            return {}
        
        stats = {
            'pending': pending_count,
            'processing': processing_count,
            'failed': failed_count,
            'sent_last_hour': sent_count
        }
        
        # ✅ Логируем статус с уровнем логирования, зависящим от значений
        if stats['pending'] > 100:
            logger.warning(f"⚠️ Large pending queue: {stats['pending']} notifications")
        elif stats['pending'] > 50:
            logger.info(f"Pending notifications: {stats['pending']}")
        else:
            logger.debug(f"Pending notifications: {stats['pending']}")
        
        if stats['processing'] > 10:
            logger.warning(f"⚠️ Many processing notifications: {stats['processing']}")
        elif stats['processing'] > 5:
            logger.info(f"Processing notifications: {stats['processing']}")
        
        if stats['failed'] > 50:
            logger.warning(f"⚠️ High failed count: {stats['failed']} notifications")
        elif stats['failed'] > 20:
            logger.info(f"Failed notifications: {stats['failed']}")
        
        # ✅ Общий health check логирование
        logger.info(
            f"📊 Health check: pending={stats['pending']}, "
            f"processing={stats['processing']}, failed={stats['failed']}, "
            f"sent_last_hour={stats['sent_last_hour']}"
        )
        
        return stats
        
    except ImportError as e:
        logger.error(f"Failed to import NotificationQueue: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error in health_check_notifications: {e}", exc_info=True)
        return {}
