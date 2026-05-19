"""
Celery application для UstozHub.

Запуск воркера (production):
    celery -A core worker -l info

Запуск Celery Beat (planner, для scheduled-уведомлений):
    celery -A core beat -l info

Запуск worker+beat в одном процессе (только dev):
    celery -A core worker -B -l info

Eager-режим для тестов и dev — установить в env:
    CELERY_TASK_ALWAYS_EAGER=True
"""
import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('ustozhub')

# Загружаем настройки из Django settings с префиксом CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматический сбор задач из tasks.py во всех INSTALLED_APPS
app.autodiscover_tasks()

# =============================================================================
# BEAT SCHEDULE (periodic tasks)
# =============================================================================
# Использует встроенный PersistentScheduler (файл celerybeat-schedule).
# Не требует django-celery-beat — нужен только для динамического расписания
# через admin (это в Phase 4).
app.conf.beat_schedule = {
    # Phase 1: освобождать слоты с истёкшим 15-мин hold
    'release-expired-holds-every-minute': {
        'task': 'teachers.release_expired_holds',
        'schedule': 60.0,
    },
    # Phase 1: помечать прошедшие confirmed-уроки как completed
    'mark-completed-lessons-every-5min': {
        'task': 'teachers.mark_completed_lessons',
        'schedule': 300.0,
    },
    # Чистка старых wizard-drafts раз в сутки в 3:00 Asia/Tashkent
    'cleanup-wizard-drafts-daily': {
        'task': 'teachers.cleanup_wizard_drafts_async',
        'schedule': crontab(hour=3, minute=0),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Тестовая задача — проверить что Celery работает.

    Вызов из shell:
        from core.celery import debug_task
        debug_task.delay()
    """
    print(f'Request: {self.request!r}')
