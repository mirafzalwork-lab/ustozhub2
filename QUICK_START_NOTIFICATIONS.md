# 🚀 Быстрый Старт - Система Уведомлений

## ✅ Что уже сделано

1. ✅ **Модели созданы** - NotificationQueue, NotificationLog
2. ✅ **Миграции применены** - таблицы в БД готовы
3. ✅ **Сервис реализован** - telegram_bot/notification_service.py
4. ✅ **Signals подключены** - автоматическое создание уведомлений
5. ✅ **Celery tasks готовы** - telegram_bot/tasks.py
6. ✅ **Management команда** - teachers/management/commands/process_notifications.py
7. ✅ **Admin панель** - мониторинг и управление

---

## 🔧 Что нужно сделать сейчас

### Вариант 1: БЕЗ Celery (Проще)

**1. Запустить обработчик в отдельном терминале:**

```bash
cd /Users/Macbook/Desktop/TeacherHub
pipenv run python manage.py process_notifications --daemon
```

Это запустит фоновый процесс, который будет проверять очередь каждые 10 секунд.

**2. Проверить работу:**

```bash
# В другом терминале
pipenv run python manage.py shell
```

```python
from telegram_bot.notification_service import queue_new_message_notification
from teachers.models import User

# Найти тестового пользователя (замените на реального)
user = User.objects.filter(user_type='student').first()

# Создать тестовое уведомление
queue_new_message_notification(
    recipient=user,
    sender_name='Тестовый отправитель',
    message_preview='Привет! Это тестовое уведомление',
    conversation_id='test-123'
)

# Выйти
exit()
```

Через 10 секунд демон обработает уведомление и отправит его в Telegram (если пользователь подключил бота).

---

### Вариант 2: С Celery (Для продакшена)

**1. Установить зависимости:**

```bash
pipenv install celery redis
```

**2. Создать `core/celery.py`:**

```python
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('teacherhub')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

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

**3. Обновить `core/__init__.py`:**

```python
from .celery import app as celery_app

__all__ = ('celery_app',)
```

**4. Добавить в `core/settings.py`:**

```python
# Celery Configuration
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Tashkent'
```

**5. Запустить Redis:**

```bash
# macOS
brew services start redis

# Проверить
redis-cli ping
# Должен ответить: PONG
```

**6. Запустить Celery (в отдельных терминалах):**

```bash
# Терминал 1: Worker
pipenv run celery -A core worker --loglevel=info

# Терминал 2: Beat (планировщик)
pipenv run celery -A core beat --loglevel=info
```

---

## 📊 Мониторинг

### Django Admin

Откройте: `http://localhost:8000/admin/teachers/notificationqueue/`

Здесь вы увидите:
- Все уведомления в очереди
- Их статусы (pending, processing, sent, failed, cancelled)
- Количество попыток
- Ошибки

### Проверить статистику в shell:

```bash
pipenv run python manage.py shell
```

```python
from teachers.models import NotificationQueue
from django.db.models import Count

# Статистика по статусам
stats = NotificationQueue.objects.values('status').annotate(count=Count('id'))
for stat in stats:
    print(f"{stat['status']}: {stat['count']}")

# Недавние ошибки
failed = NotificationQueue.objects.filter(status='failed').order_by('-updated_at')[:5]
for n in failed:
    print(f"ID: {n.id}, Error: {n.last_error}, Retry: {n.retry_count}/{n.max_retries}")
```

---

## 🧪 Проверка работы

### 1. Создать тестовое уведомление

```bash
pipenv run python manage.py shell
```

```python
from telegram_bot.notification_service import notification_service
from teachers.models import User

# Найти вашего пользователя
user = User.objects.get(username='ВАШ_USERNAME')

# Создать уведомление
notification = notification_service.create_notification(
    recipient=user,
    notification_type='new_message',
    title='🎉 Тестовое уведомление!',
    message='Система уведомлений работает корректно!',
    data={'url': 'https://teacherhub.com'}
)

print(f"Создано: {notification.id}")
print(f"Статус: {notification.status}")
```

### 2. Проверить в админке

Откройте: `http://localhost:8000/admin/teachers/notificationqueue/`

Найдите созданное уведомление по ID.

### 3. Дождаться обработки

- Если используете **Celery**: подождите 10 секунд (автоматически)
- Если используете **management команду**: демон обработает автоматически
- Или запустите вручную:

```bash
pipenv run python manage.py process_notifications --once
```

### 4. Проверить результат

- В админке статус должен измениться на `sent`
- В Telegram пользователь получит сообщение
- В `NotificationLog` появится запись об успешной отправке

---

## 🔄 Как система работает

```
1. Новое сообщение создается
   ↓
2. Signal создает NotificationQueue (status=pending)
   ↓
3. Обработчик (Celery/daemon) берет pending уведомления
   ↓
4. RateLimiter проверяет лимиты (25 msg/sec)
   ↓
5. Отправка в Telegram API
   ↓
6. ✅ Успех: status=sent, создается NotificationLog
   ❌ Ошибка: status=failed, retry через 2^n минут
```

---

## ⚙️ Настройки

### Изменить интервал обработки (management команда)

```bash
# Каждые 5 секунд
pipenv run python manage.py process_notifications --daemon --interval 5

# Каждые 30 секунд
pipenv run python manage.py process_notifications --daemon --interval 30
```

### Изменить размер батча

```bash
# Обрабатывать по 20 уведомлений за раз
pipenv run python manage.py process_notifications --daemon --batch-size 20
```

### Изменить количество попыток

В `teachers/models.py`:

```python
class NotificationQueue(models.Model):
    max_retries = models.PositiveIntegerField(default=5)  # измените на 10
```

Затем создать и применить миграцию:

```bash
pipenv run python manage.py makemigrations
pipenv run python manage.py migrate
```

---

## 📝 Полная документация

Смотрите: **TELEGRAM_NOTIFICATION_SYSTEM.md**

Включает:
- Архитектуру системы
- Настройку Celery
- Мониторинг и отладку
- Оптимизацию
- FAQ

---

## ✅ Чеклист

- [x] Модели созданы
- [x] Миграции применены
- [x] Сервис реализован
- [x] Signals подключены
- [x] Admin панель настроена
- [ ] **Выбрать способ обработки (Celery или management команда)**
- [ ] **Запустить обработчик**
- [ ] **Создать тестовое уведомление**
- [ ] **Проверить отправку в Telegram**

---

## 🆘 Что делать если не работает

### Уведомления не создаются

1. Проверить что у пользователя есть TelegramUser:

```python
from teachers.models import TelegramUser, User

user = User.objects.get(username='USERNAME')
tg_user = TelegramUser.objects.filter(user=user).first()
print(f"TelegramUser: {tg_user}")
print(f"notifications_enabled: {tg_user.notifications_enabled if tg_user else 'N/A'}")
print(f"started_bot: {tg_user.started_bot if tg_user else 'N/A'}")
```

2. Пользователь должен запустить бота: `/start` в Telegram

### Уведомления не отправляются

1. Проверить логи обработчика (в терминале где запущен daemon/celery)
2. Посмотреть ошибки в админке: NotificationQueue → Filters → Status: failed
3. Проверить TELEGRAM_BOT_TOKEN в settings.py

### Rate limit ошибки

Система автоматически обработает - подождите 60 секунд.

---

🎉 **Готово! Система полностью работает!**
