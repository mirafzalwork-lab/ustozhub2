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

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('ustozhub')

# Загружаем настройки из Django settings с префиксом CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматический сбор задач из tasks.py во всех INSTALLED_APPS
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Тестовая задача — проверить что Celery работает.

    Вызов из shell:
        from core.celery import debug_task
        debug_task.delay()
    """
    print(f'Request: {self.request!r}')
