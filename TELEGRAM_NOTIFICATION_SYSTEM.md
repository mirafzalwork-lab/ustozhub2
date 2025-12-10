# 🔔 Система Telegram Уведомлений - Полное Руководство

## 📋 Обзор

Профессиональная система уведомлений через Telegram с:
- ✅ **Очередь с retry логикой** - экспоненциальная задержка (2^n минут)
- ✅ **Rate limiting** - 25 сообщений/сек, 15 сообщений/мин на пользователя
- ✅ **Идемпотентность** - защита от дублирования
- ✅ **Батчинг** - обработка групп уведомлений
- ✅ **Логирование** - полная история попыток доставки
- ✅ **Мониторинг** - статусы, ошибки, метрики

---

## 🏗️ Архитектура

### Компоненты

```
┌─────────────────────────────────────────────────────────────┐
│                    Django Application                        │
│  ┌──────────────┐      ┌──────────────┐                     │
│  │   Signal     │─────▶│  Notification│                     │
│  │ (new message)│      │    Service   │                     │
│  └──────────────┘      └──────┬───────┘                     │
│                               │                              │
│                               ▼                              │
│                    ┌──────────────────┐                      │
│                    │ NotificationQueue│ (Database)           │
│                    └─────────┬────────┘                      │
│                              │                               │
└──────────────────────────────┼───────────────────────────────┘
                               │
                               ▼
         ┌────────────────────────────────────┐
         │  Processing (выберите один способ) │
         │  ┌─────────────┐  ┌──────────────┐│
         │  │   Celery    │  │  Management  ││
         │  │   Worker    │  │   Command    ││
         │  └──────┬──────┘  └──────┬───────┘│
         └─────────┼────────────────┼─────────┘
                   │                │
                   └────────┬───────┘
                            ▼
                   ┌─────────────────┐
                   │  Rate Limiter   │
                   └────────┬────────┘
                            ▼
                   ┌─────────────────┐
                   │  Telegram API   │
                   └────────┬────────┘
                            ▼
                   ┌─────────────────┐
                   │NotificationLog  │ (Database)
                   └─────────────────┘
```

### Модели

**NotificationQueue** - Очередь уведомлений
- `id` (UUID) - Уникальный ID
- `recipient` (FK User) - Получатель
- `notification_type` (choices) - Тип: new_message, new_review, etc.
- `title` (str) - Заголовок
- `message` (text) - Текст уведомления
- `data` (JSON) - Дополнительные данные (URL, кнопки)
- `status` (choices) - pending, processing, sent, failed, cancelled
- `retry_count` (int) - Счётчик попыток
- `max_retries` (int) - Макс попыток (default: 5)
- `idempotency_key` (unique str) - Защита от дублей
- `scheduled_at` (datetime) - Когда отправлять
- `last_error` (text) - Последняя ошибка

**NotificationLog** - Аудит лог
- `notification` (FK NotificationQueue) - Уведомление
- `attempt_number` (int) - Номер попытки
- `status` (str) - success, error, skipped
- `error_message` (text) - Сообщение об ошибке
- `telegram_message_id` (int) - ID в Telegram
- `processing_time_ms` (int) - Время обработки
- `timestamp` (datetime) - Время попытки

---

## 🚀 Установка и Настройка

### Шаг 1: Проверить модели

Модели уже созданы в `teachers/models.py`. Миграции применены.

### Шаг 2: Настроить Celery (Опционально)

#### Установить Celery и Redis

```bash
pipenv install celery redis
```

#### Создать `core/celery.py`

```python
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('teacherhub')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Расписание задач
app.conf.beat_schedule = {
    'process-notification-queue': {
        'task': 'process_notification_queue',
        'schedule': 10.0,  # каждые 10 секунд
    },
    'retry-failed-notifications': {
        'task': 'retry_failed_notifications',
        'schedule': crontab(minute=0),  # каждый час
    },
    'cleanup-notification-logs': {
        'task': 'cleanup_old_notification_logs',
        'schedule': crontab(hour=3, minute=0),  # 3:00 ночи
    },
    'cleanup-old-notifications': {
        'task': 'cleanup_old_notifications',
        'schedule': crontab(hour=3, minute=30),  # 3:30 ночи
    },
    'cancel-stuck-notifications': {
        'task': 'cancel_stuck_notifications',
        'schedule': 900.0,  # каждые 15 минут
    },
}
```

#### Обновить `core/__init__.py`

```python
from .celery import app as celery_app

__all__ = ('celery_app',)
```

#### Добавить в `core/settings.py`

```python
# Celery Configuration
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Tashkent'
```

#### Запустить Celery

```bash
# Worker
pipenv run celery -A core worker --loglevel=info

# Beat (планировщик)
pipenv run celery -A core beat --loglevel=info
```

### Шаг 3: Fallback без Celery

Если не хотите настраивать Celery, используйте management команду:

```bash
# Обработать один раз
pipenv run python manage.py process_notifications

# Режим демона (непрерывно)
pipenv run python manage.py process_notifications --daemon

# С кастомным интервалом (каждые 5 секунд)
pipenv run python manage.py process_notifications --daemon --interval 5

# Увеличенный размер батча
pipenv run python manage.py process_notifications --daemon --batch-size 20
```

#### Автозапуск через systemd (Linux)

Создать `/etc/systemd/system/teacherhub-notifications.service`:

```ini
[Unit]
Description=TeacherHub Telegram Notifications
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/TeacherHub
Environment="PATH=/path/to/.local/share/virtualenvs/TeacherHub-xxx/bin"
ExecStart=/path/to/.local/share/virtualenvs/TeacherHub-xxx/bin/python manage.py process_notifications --daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Запустить:
```bash
sudo systemctl enable teacherhub-notifications
sudo systemctl start teacherhub-notifications
sudo systemctl status teacherhub-notifications
```

---

## 📝 Использование

### Создать уведомление вручную

```python
from telegram_bot.notification_service import notification_service
from teachers.models import User

recipient = User.objects.get(username='ivan')

notification = notification_service.create_notification(
    recipient=recipient,
    notification_type='new_message',
    title='💬 Новое сообщение!',
    message='От: **Петр Петров**\n\nПривет, как дела?',
    data={
        'sender_name': 'Петр Петров',
        'conversation_id': '123',
        'url': 'https://teacherhub.com/conversations/123/',
        'button_text': '📬 Открыть диалог'
    }
)
```

### Использовать удобную функцию

```python
from telegram_bot.notification_service import queue_new_message_notification
from teachers.models import User

recipient = User.objects.get(username='ivan')

queue_new_message_notification(
    recipient=recipient,
    sender_name='Петр Петров',
    message_preview='Привет, как дела?',
    conversation_id='123'
)
```

### Автоматическое создание через Signal

Уведомления создаются автоматически при создании нового сообщения.  
Signal уже настроен в `teachers/signals.py`.

### Мониторинг очереди

```python
from teachers.models import NotificationQueue

# Статистика
pending = NotificationQueue.objects.filter(status='pending').count()
processing = NotificationQueue.objects.filter(status='processing').count()
sent = NotificationQueue.objects.filter(status='sent').count()
failed = NotificationQueue.objects.filter(status='failed').count()

print(f"Pending: {pending}, Processing: {processing}, Sent: {sent}, Failed: {failed}")

# Последние ошибки
recent_failures = NotificationQueue.objects.filter(
    status='failed'
).order_by('-updated_at')[:10]

for notification in recent_failures:
    print(f"ID: {notification.id}")
    print(f"Recipient: {notification.recipient.username}")
    print(f"Error: {notification.last_error}")
    print(f"Retry count: {notification.retry_count}/{notification.max_retries}")
    print("---")
```

---

## 🛠️ Административная панель

### Регистрация в Admin

Добавить в `teachers/admin.py`:

```python
from django.contrib import admin
from teachers.models import NotificationQueue, NotificationLog

class NotificationLogInline(admin.TabularInline):
    model = NotificationLog
    extra = 0
    readonly_fields = ('attempt_number', 'status', 'error_message', 'telegram_message_id', 'processing_time_ms', 'timestamp')
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False

@admin.register(NotificationQueue)
class NotificationQueueAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient', 'notification_type', 'status', 'retry_count', 'scheduled_at', 'created_at')
    list_filter = ('status', 'notification_type', 'created_at')
    search_fields = ('recipient__username', 'recipient__email', 'title', 'message')
    readonly_fields = ('id', 'idempotency_key', 'created_at', 'updated_at', 'sent_at', 'processing_started_at')
    inlines = [NotificationLogInline]
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('id', 'recipient', 'notification_type', 'status', 'idempotency_key')
        }),
        ('Содержимое', {
            'fields': ('title', 'message', 'data')
        }),
        ('Настройки retry', {
            'fields': ('retry_count', 'max_retries', 'last_error')
        }),
        ('Временные метки', {
            'fields': ('scheduled_at', 'created_at', 'updated_at', 'processing_started_at', 'sent_at')
        }),
    )
    
    actions = ['resend_failed', 'cancel_pending']
    
    def resend_failed(self, request, queryset):
        count = 0
        for notification in queryset.filter(status='failed'):
            if notification.can_retry():
                notification.status = 'pending'
                notification.scheduled_at = timezone.now()
                notification.save()
                count += 1
        self.message_user(request, f'Запланировано повторно: {count} уведомлений')
    resend_failed.short_description = 'Повторить отправку failed уведомлений'
    
    def cancel_pending(self, request, queryset):
        count = queryset.filter(status='pending').update(status='cancelled')
        self.message_user(request, f'Отменено: {count} уведомлений')
    cancel_pending.short_description = 'Отменить pending уведомления'

@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('notification', 'attempt_number', 'status', 'processing_time_ms', 'timestamp')
    list_filter = ('status', 'timestamp')
    search_fields = ('notification__id', 'error_message')
    readonly_fields = ('notification', 'attempt_number', 'status', 'error_message', 'telegram_message_id', 'response_data', 'processing_time_ms', 'timestamp')
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
```

---

## 📊 Мониторинг и Отладка

### Проверить статус очереди

```bash
pipenv run python manage.py shell
```

```python
from teachers.models import NotificationQueue, NotificationLog
from django.db.models import Count

# Статистика по статусам
stats = NotificationQueue.objects.values('status').annotate(count=Count('id'))
for stat in stats:
    print(f"{stat['status']}: {stat['count']}")

# Недавние ошибки
recent_errors = NotificationLog.objects.filter(
    status='error'
).select_related('notification').order_by('-timestamp')[:20]

for log in recent_errors:
    print(f"[{log.timestamp}] Notification {log.notification.id}")
    print(f"  Error: {log.error_message}")
    print(f"  Attempt: {log.attempt_number}")
    print()
```

### Логи

```bash
# Celery worker logs
tail -f /var/log/celery/worker.log

# Management command logs (если запущен через systemd)
journalctl -u teacherhub-notifications -f

# Django logs
tail -f /path/to/logs/django.log
```

### Метрики производительности

```python
from teachers.models import NotificationLog
from django.db.models import Avg, Max, Min

# Средняя скорость обработки
avg_time = NotificationLog.objects.filter(
    status='success'
).aggregate(avg=Avg('processing_time_ms'))

print(f"Средняя скорость: {avg_time['avg']}мс")

# Самые медленные
slowest = NotificationLog.objects.filter(
    status='success'
).order_by('-processing_time_ms')[:10]

for log in slowest:
    print(f"Notification {log.notification.id}: {log.processing_time_ms}мс")
```

---

## ⚡ Оптимизация

### Rate Limiting

Настроить в `telegram_bot/notification_service.py`:

```python
# Изменить лимиты
rate_limiter = RateLimiter(
    max_per_second=30,  # Telegram лимит: 30 msg/sec
    max_per_minute_per_user=20  # 20 msg/min на пользователя
)
```

### Размер батча

```python
# Для высокой нагрузки
notification_service.process_queue_batch_sync(batch_size=50)

# Для низкой нагрузки (меньше DB queries)
notification_service.process_queue_batch_sync(batch_size=5)
```

### Retry стратегия

Изменить в `teachers/models.py`:

```python
class NotificationQueue(models.Model):
    max_retries = models.PositiveIntegerField(default=5)  # увеличить до 10
    
    def calculate_next_retry_delay(self):
        # Более агрессивная задержка
        delay_minutes = 5 * (2 ** self.retry_count)
        return datetime.timedelta(minutes=min(delay_minutes, 120))
```

---

## 🔒 Безопасность

### Защита от дублирования (Идемпотентность)

- `idempotency_key` генерируется из `recipient_id + notification_type + data`
- Проверка существующих уведомлений перед созданием
- Unique constraint на уровне БД

### Приватность

- Сообщения удаляются через 90 дней (настраивается)
- Логи удаляются через 30 дней
- Только владелец может получать уведомления

### Opt-out

Пользователи могут отключить уведомления в Telegram боте:
```
/notifications
```

---

## 🧪 Тестирование

### Ручная проверка

```bash
pipenv run python manage.py shell
```

```python
from telegram_bot.notification_service import queue_new_message_notification
from teachers.models import User

# Найти тестового пользователя
user = User.objects.first()

# Создать тестовое уведомление
queue_new_message_notification(
    recipient=user,
    sender_name='Test Sender',
    message_preview='This is a test notification',
    conversation_id='test-123'
)

# Обработать очередь
from telegram_bot.notification_service import process_notification_queue
sent = process_notification_queue()
print(f"Sent: {sent}")
```

### Unit тесты

```python
from django.test import TestCase
from teachers.models import User, NotificationQueue
from telegram_bot.notification_service import notification_service

class NotificationServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='password123'
        )
    
    def test_create_notification(self):
        notification = notification_service.create_notification(
            recipient=self.user,
            notification_type='new_message',
            title='Test',
            message='Test message',
            data={'test': 'data'}
        )
        
        self.assertIsNotNone(notification)
        self.assertEqual(notification.status, 'pending')
    
    def test_idempotency(self):
        # Создать два одинаковых уведомления
        n1 = notification_service.create_notification(
            recipient=self.user,
            notification_type='new_message',
            title='Test',
            message='Test',
            data={'id': '123'}
        )
        
        n2 = notification_service.create_notification(
            recipient=self.user,
            notification_type='new_message',
            title='Test',
            message='Test',
            data={'id': '123'}
        )
        
        # Должны вернуть одно и то же уведомление
        self.assertEqual(n1.id, n2.id)
```

---

## 📚 FAQ

**Q: Что если Telegram API недоступен?**  
A: Уведомления остаются в очереди со статусом `failed`. Система автоматически повторит попытку с экспоненциальной задержкой.

**Q: Как масштабировать для высокой нагрузки?**  
A: 
- Увеличить количество Celery workers
- Увеличить `batch_size`
- Использовать Redis для rate limiting
- Настроить индексы БД (уже настроены)

**Q: Нужен ли Celery обязательно?**  
A: Нет. Можно использовать management команду с `--daemon` флагом.

**Q: Как отключить уведомления для всех?**  
A: В `core/settings.py` установить `TELEGRAM_BOT_TOKEN = None`

**Q: Что делать если пользователь заблокировал бота?**  
A: Система автоматически пометит уведомление как `cancelled` и больше не будет пытаться отправлять.

---

## 📧 Поддержка

При возникновении проблем:
1. Проверить логи (`journalctl -u teacherhub-notifications -f`)
2. Проверить статус очереди в Django Admin
3. Посмотреть `NotificationLog` для деталей ошибок
4. Проверить настройки Telegram бота

---

## ✅ Чеклист Запуска

- [ ] Модели созданы и миграции применены
- [ ] `TELEGRAM_BOT_TOKEN` установлен в settings
- [ ] Выбран способ обработки (Celery или management команда)
- [ ] Если Celery:
  - [ ] Redis установлен и запущен
  - [ ] `core/celery.py` создан
  - [ ] Celery worker запущен
  - [ ] Celery beat запущен
- [ ] Если management команда:
  - [ ] Команда запущена в daemon режиме
  - [ ] Systemd service настроен (опционально)
- [ ] Админ панель настроена
- [ ] Тестовое уведомление отправлено успешно
- [ ] Логи проверены на ошибки

---

🎉 **Система готова к работе!**
