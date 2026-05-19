"""
Celery-задачи приложения teachers.

В Phase 0 — только skeleton с health-check.
В Phase 4 сюда добавятся:
    • send_lesson_reminder(lesson_id, hours_before)
    • release_expired_booking_holds()
    • send_email_notification(user_id, template, context)
    • daily_reminder_dispatch()
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='teachers.health_check')
def health_check() -> dict:
    """Простой ping для проверки что Celery worker жив и видит наши задачи."""
    from django.utils import timezone
    return {'ok': True, 'at': timezone.now().isoformat()}


@shared_task(name='teachers.cleanup_wizard_drafts_async')
def cleanup_wizard_drafts_async(days: int = 14) -> int:
    """Удалить устаревшие WizardDraft. Дублирует management-команду,
    но удобно для Celery Beat-расписания."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import WizardDraft

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = WizardDraft.objects.filter(updated_at__lt=cutoff).delete()
    logger.info(f'cleanup_wizard_drafts_async: deleted {deleted} drafts')
    return deleted


@shared_task(name='teachers.release_expired_holds')
def release_expired_holds() -> int:
    """
    Освобождает слоты с истёкшим 15-мин hold.
    Запускается Celery Beat каждую минуту.

    Логика: для каждого Booking со status='pending' и expires_at<now
    переводим в 'expired', а связанный TimeSlot — в 'free'.
    """
    from django.utils import timezone
    from .models import Booking

    now = timezone.now()
    expired = Booking.objects.filter(
        status='pending',
        expires_at__lt=now,
    ).select_related('slot')

    count = 0
    for booking in expired:
        try:
            booking.expire()
            count += 1
        except Exception as e:
            logger.error(
                f'release_expired_holds: failed to expire booking {booking.pk}: {e}',
                exc_info=True,
            )

    if count:
        logger.info(f'release_expired_holds: expired {count} bookings')
    return count


@shared_task(name='teachers.mark_completed_lessons')
def mark_completed_lessons() -> int:
    """
    Помечает confirmed-бронирования как completed после end_at слота.
    Запускается Celery Beat каждые 5 минут.
    """
    from django.utils import timezone
    from .models import Booking

    now = timezone.now()
    to_complete = Booking.objects.filter(
        status='confirmed',
        slot__end_at__lt=now,
    ).select_related('slot')

    count = 0
    for booking in to_complete:
        try:
            booking.mark_completed()
            count += 1
        except Exception as e:
            logger.error(
                f'mark_completed_lessons: failed for booking {booking.pk}: {e}',
                exc_info=True,
            )

    if count:
        logger.info(f'mark_completed_lessons: completed {count} lessons')
    return count
