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
