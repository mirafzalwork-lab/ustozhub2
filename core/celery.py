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
    # Phase 4: напоминания об уроках (T-24h / T-3h / T-10min)
    'send-lesson-reminders-every-minute': {
        'task': 'teachers.send_lesson_reminders',
        'schedule': 60.0,
    },
    # Чистка старых wizard-drafts раз в сутки в 3:00 Asia/Tashkent
    'cleanup-wizard-drafts-daily': {
        'task': 'teachers.cleanup_wizard_drafts_async',
        'schedule': crontab(hour=3, minute=0),
    },
    # Phase 4: выплаты учителям за завершённые subscription-уроки после grace window
    'release-pending-payouts-every-5min': {
        'task': 'billing.release_pending_payouts',
        'schedule': 300.0,
    },
    # ТЗ flow: одобренные, но не оплаченные в срок заявки → EXPIRED
    'expire-unpaid-approvals-every-15min': {
        'task': 'billing.expire_unpaid_approvals',
        'schedule': 900.0,
    },
    # v2 Шаг 1: истёкшие активные подписки → слить зависший escrow ученику
    'settle-expired-subscriptions-hourly': {
        'task': 'billing.settle_expired_subscriptions',
        'schedule': 3600.0,
    },
    # Страховка: дозакрыть потерянные возвраты за пробные (сбой между сменой
    # статуса и refund во view). Идемпотентно.
    'reconcile-orphaned-refunds-every-30min': {
        'task': 'billing.reconcile_orphaned_refunds',
        'schedule': 1800.0,
    },
    # Ночная сверка денежного инварианта balance == SUM(transactions).
    'reconcile-wallet-balances-daily': {
        'task': 'billing.reconcile_wallet_balances',
        'schedule': crontab(hour=4, minute=0),
    },
    # --- Обслуживание очереди Telegram-уведомлений ---
    # Саму очередь обрабатывает демон telegram-bot.service (process_notifications
    # --daemon). Здесь — только сервисные задачи, которых демон не делает.
    'retry-failed-notifications-hourly': {
        'task': 'retry_failed_notifications',
        'schedule': crontab(minute=0),
    },
    'cancel-stuck-notifications-every-15min': {
        'task': 'cancel_stuck_notifications',
        'schedule': 900.0,
    },
    'health-check-notifications-every-5min': {
        'task': 'health_check_notifications',
        'schedule': 300.0,
    },
    'cleanup-old-notification-logs-daily': {
        'task': 'cleanup_old_notification_logs',
        'schedule': crontab(hour=3, minute=0),
    },
    'cleanup-old-notifications-daily': {
        'task': 'cleanup_old_notifications',
        'schedule': crontab(hour=3, minute=30),
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
